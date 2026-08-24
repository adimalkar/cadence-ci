"""Missing dependency cache, and cache keys that can never hit.

Two findings share this module because they are the same measurement read differently:

  * **No cache at all** — no `actions/cache` step, no `cache:` input on a setup action,
    and an install step whose duration is *flat*. Flatness is the tell: a working cache
    is bimodal (fast on hit, slow on miss), so consistently-slow-with-low-variance means
    nothing is being restored.
  * **A key that cannot hit** — a key interpolating `github.run_id` (or `github.sha`)
    is unique per run, so the cache is written every time and read never. That is a
    deterministic bug, not a tuning problem, and it earns high confidence on config
    alone.

Savings here are **projection**, never replay: we have no observation of this repo in a
cached state, so the number is an estimate with a range and a named basis.
"""

from __future__ import annotations

import re

from cadence.detectors.base import EvidenceDraft, FindingDraft
from cadence.detectors.context import AuditContext
from cadence.simulate import duration_is_flat, project_cache_savings
from cadence.workflow import Job, Workflow

DETECTOR_ID = "waste.dependency_cache"
DETECTOR_VERSION = "dependency_cache@1"

_CACHE_ACTION = "actions/cache"
# Setup actions with built-in caching; `cache:` set on any of them counts as cached.
_SETUP_ACTIONS = {
    "actions/setup-node", "actions/setup-python", "actions/setup-java",
    "actions/setup-go", "actions/setup-dotnet", "ruby/setup-ruby",
    "astral-sh/setup-uv", "pnpm/action-setup",
}
# Dedicated caching actions that make an explicit actions/cache step unnecessary.
_CACHE_EQUIVALENT = {
    "Swatinem/rust-cache", "actions/cache/restore", "buildjet/cache",
    "runs-on/cache", "useblacksmith/cache", "actions/setup-node",
}

_INSTALL_PATTERNS = re.compile(
    r"\b(npm (ci|install)|yarn install|pnpm install|bundle install|"
    r"pip install|poetry install|uv sync|uv pip install|"
    r"go mod download|cargo fetch|mvn .*dependency:go-offline|gradle .*dependencies|"
    r"composer install|apt-get install)\b",
    re.IGNORECASE,
)

# Keys interpolating these are unique per run and can never be restored.
_NEVER_HITS = re.compile(r"github\s*\.\s*(run_id|run_number|run_attempt|sha)\b")


class DependencyCacheDetector:
    id = DETECTOR_ID
    version = DETECTOR_VERSION

    def run(self, ctx: AuditContext) -> list[FindingDraft]:
        drafts: list[FindingDraft] = []
        for wf in ctx.workflows:
            if wf.parse_error:
                continue
            for job_key, job in wf.jobs.items():
                drafts.extend(self._never_hits(wf, job_key, job))
                draft = self._missing_cache(ctx, wf, job_key, job)
                if draft is not None:
                    drafts.append(draft)
        return drafts

    # ── a key that can never hit ───────────────────────────────────────────────
    def _never_hits(self, wf: Workflow, job_key: str, job: Job) -> list[FindingDraft]:
        out: list[FindingDraft] = []
        for step in job.steps:
            if step.action != _CACHE_ACTION:
                continue
            key = str(step.with_.get("key", ""))
            if not _NEVER_HITS.search(key):
                continue
            out.append(
                FindingDraft(
                    kind="cache_key_never_hits",
                    module="waste",
                    severity=4,
                    confidence=0.98,  # config alone is conclusive here
                    dedupe_key=f"cache_key_never_hits:{wf.path}:{job_key}:{step.index}",
                    title=(
                        f"Cache key in `{job_key}` is unique per run — written every "
                        f"run, restored never"
                    ),
                    detector_version=DETECTOR_VERSION,
                    suggested_action=(
                        "Key on content, not the run: "
                        "`${{ runner.os }}-${{ hashFiles('**/lockfile') }}`. "
                        "A key containing github.run_id/sha cannot match a previous run."
                    ),
                    savings=None,  # the waste is the whole restore; sizing needs a baseline
                    evidence=[
                        EvidenceDraft(
                            kind="code_range",
                            file_path=wf.path,
                            line_start=step.line,
                            line_end=step.line,
                            payload={"key": key[:200], "job": job_key},
                        )
                    ],
                )
            )
        return out

    # ── no cache configured at all ─────────────────────────────────────────────
    def _missing_cache(
        self, ctx: AuditContext, wf: Workflow, job_key: str, job: Job
    ) -> FindingDraft | None:
        if _job_has_caching(job):
            return None

        install = _install_step(job)
        if install is None:
            return None

        series = ctx.step_series.get((job_key, install.name or install.run or ""))
        if series is None:
            series = _best_series_for(ctx, job_key)
        if series is None or len(series.durations) < 5:
            return None

        # Flat duration is the positive signal. Without it we cannot distinguish "no
        # cache" from "cache configured elsewhere in a way we did not parse".
        if not duration_is_flat(series.durations):
            return None

        savings = project_cache_savings(series.durations)
        if savings is None or savings.high < 5.0:
            return None

        return FindingDraft(
            kind="no_dependency_cache",
            module="waste",
            severity=3,
            confidence=0.75,  # projection-based; lower than the config-only rules
            dedupe_key=f"no_dependency_cache:{wf.path}:{job_key}",
            title=f"`{job_key}` installs dependencies cold on every run — no cache configured",
            detector_version=DETECTOR_VERSION,
            suggested_action=(
                "Add actions/cache keyed on `${{ runner.os }}-${{ hashFiles('<lockfile>') }}`, "
                "or set `cache:` on the setup action for this ecosystem."
            ),
            savings=savings,
            parallel_jobs=1.0,
            evidence=[
                EvidenceDraft(
                    kind="code_range",
                    file_path=wf.path,
                    line_start=install.line,
                    line_end=install.line,
                    payload={"job": job_key, "step": install.name or install.run},
                ),
                EvidenceDraft(
                    kind="timing_series",
                    payload={
                        "step": series.step_name,
                        "durations": [round(d, 1) for d in series.durations[:100]],
                        "flat": True,
                        "detail": savings.detail,
                    },
                ),
                EvidenceDraft(
                    kind="counterfactual",
                    payload={
                        "basis": savings.basis.value,
                        "low_seconds": round(savings.low, 1),
                        "high_seconds": round(savings.high, 1),
                        "method": "projected from cold-install p50; no cached state observed",
                    },
                ),
            ],
        )


def _job_has_caching(job: Job) -> bool:
    for step in job.steps:
        action = step.action
        if action is None:
            continue
        if action == _CACHE_ACTION or action in _CACHE_EQUIVALENT:
            return True
        if action in _SETUP_ACTIONS and step.with_.get("cache"):
            return True
    return False


def _install_step(job: Job):
    for step in job.steps:
        if step.run and _INSTALL_PATTERNS.search(step.run):
            return step
    return None


def _best_series_for(ctx: AuditContext, job_key: str):
    """Longest-running observed step for this job, as a stand-in when the step name in
    config does not match the recorded one (composite actions rename steps)."""
    best = None
    for (jk, _name), series in ctx.step_series.items():
        if jk != job_key or not series.durations:
            continue
        if best is None or sum(series.durations) > sum(best.durations):
            best = series
    return best
