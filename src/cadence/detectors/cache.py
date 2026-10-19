"""Missing dependency cache, and cache keys that can never hit.

Two findings share this module because they are the same measurement read differently:

  * **No cache at all** — no `actions/cache` step, no `cache:` input on a setup action,
    and an install step whose duration is *flat*. Flatness is the tell: a working cache
    is bimodal (fast on hit, slow on miss), so consistently-slow-with-low-variance means
    nothing is being restored.
  * **A key that cannot hit** — a key interpolating `github.run_id` (or `github.sha`)
    is unique per run, so a fresh run never restores it. That is *only* a bug when nothing
    else restores the entry, and on the corpus nothing-else was the exception: all 9
    flagged steps were working caches (CAVEATS 58). Two patterns restore a per-run key:
    `restore-keys:` (restore the newest entry by prefix, save a fresh one each run — the
    documented pattern for incremental caches), and a *handoff*, where a later job in the
    same run, or another workflow, restores the exact key to pass a build along. Either one
    silences the finding.

Savings here are **projection**, never replay: we have no observation of this repo in a
cached state, so the number is an estimate with a range and a named basis.
"""

from __future__ import annotations

import re

from cadence.detectors.base import EvidenceDraft, FindingDraft
from cadence.detectors.context import AuditContext, RootPackageJson
from cadence.simulate import duration_is_flat, project_cache_savings
from cadence.workflow import Job, Step, Workflow

DETECTOR_ID = "waste.dependency_cache"
DETECTOR_VERSION = "dependency_cache@3"

_CACHE_ACTION = "actions/cache"
# Setup actions with built-in caching; `cache:` set on any of them counts as cached.
_SETUP_ACTIONS = {
    "actions/setup-node", "actions/setup-python", "actions/setup-java",
    "actions/setup-go", "actions/setup-dotnet", "ruby/setup-ruby",
    "astral-sh/setup-uv", "pnpm/action-setup",
}
# Dedicated caching actions that make an explicit actions/cache step unnecessary.
# Not `actions/setup-node`: until @3 it sat here, which marked every setup-node job as
# cached. Without `cache:` it caches only in one narrow case -- see setup_node_auto_caches.
_CACHE_EQUIVALENT = {
    "Swatinem/rust-cache", "actions/cache/restore", "buildjet/cache",
    "runs-on/cache", "useblacksmith/cache",
}
_SETUP_NODE = "actions/setup-node"
_CHECKOUT = "actions/checkout"
# setup-node gained automatic caching in v5.0.0 (2025-09-04).
_SETUP_NODE_AUTO_CACHE_MAJOR = 5
_MAJOR = re.compile(r"^v?(\d+)(\.|$)")

_INSTALL_PATTERNS = re.compile(
    r"\b(npm (ci|install)|yarn install|pnpm install|bundle install|"
    r"pip install|poetry install|uv sync|uv pip install|"
    r"go mod download|cargo fetch|mvn .*dependency:go-offline|gradle .*dependencies|"
    r"composer install|apt-get install)\b",
    re.IGNORECASE,
)

# An expression that *is* one of these -- not one that merely mentions it, since
# `github.head_ref || github.run_id` is per-branch on a pull request -- makes the key
# unique at that scope. run_id and run_number survive a re-run; run_attempt does not.
_UNIQUE_SCOPE = {
    "github.run_id": "run",
    "github.run_number": "run",
    "github.run_attempt": "attempt",
    "github.sha": "commit",
}
# Steps that read a cache entry back, and so can make a per-run key hit.
_RESTORERS = {_CACHE_ACTION, "actions/cache/restore"}
_EXPR = re.compile(r"\$\{\{(.*?)\}\}", re.S)


class DependencyCacheDetector:
    id = DETECTOR_ID
    version = DETECTOR_VERSION

    def run(self, ctx: AuditContext) -> list[FindingDraft]:
        drafts: list[FindingDraft] = []
        # Every restoring step in the repo: a handoff can cross workflows (`workflow_run`).
        restorers = [
            step
            for wf in ctx.workflows if not wf.parse_error
            for job in wf.jobs.values()
            for step in job.steps if step.action in _RESTORERS
        ]
        for wf in ctx.workflows:
            if wf.parse_error:
                continue
            for job_key, job in wf.jobs.items():
                drafts.extend(self._never_hits(wf, job_key, job, restorers))
                draft = self._missing_cache(ctx, wf, job_key, job)
                if draft is not None:
                    drafts.append(draft)
        return drafts

    # ── a key that can never hit ───────────────────────────────────────────────
    def _never_hits(
        self, wf: Workflow, job_key: str, job: Job, restorers: list[Step]
    ) -> list[FindingDraft]:
        out: list[FindingDraft] = []
        for step in job.steps:
            if step.action != _CACHE_ACTION:
                continue
            key = str(step.with_.get("key", ""))
            scope = unique_scope(key)
            if scope is None:
                continue
            if _restore_keys(step):
                continue  # restores the newest entry by prefix: a working cache
            if any(_restores(other, step, key) for other in restorers):
                continue  # handed to another job or workflow, by key or by prefix
            out.append(
                FindingDraft(
                    kind="cache_key_never_hits",
                    module="waste",
                    severity=4,
                    # Down from 0.98: the rule was 0 for 9 on the corpus before the two
                    # restore paths were checked (CAVEATS 58). What is left is structural,
                    # but "nothing restores it" is a claim about the files we could see.
                    confidence=0.9,
                    dedupe_key=f"cache_key_never_hits:{wf.path}:{job_key}:{step.index}",
                    title=_NEVER_HITS_TITLE[scope].format(job=job_key),
                    detector_version=DETECTOR_VERSION,
                    suggested_action=(
                        "Key on content, not the run: "
                        "`${{ runner.os }}-${{ hashFiles('**/lockfile') }}`. If a fresh "
                        "entry every run is the point, add `restore-keys:` with the key's "
                        "prefix so the newest one is restored."
                    ),
                    savings=None,  # the waste is the whole restore; sizing needs a baseline
                    evidence=[
                        EvidenceDraft(
                            kind="code_range",
                            file_path=wf.path,
                            line_start=step.line,
                            line_end=step.line,
                            payload={
                                "key": key[:200],
                                "job": job_key,
                                "unique_per": scope,
                                "restore_keys": None,
                                "restoring_steps_checked": len(restorers) - 1,
                            },
                        )
                    ],
                )
            )
        return out

    # ── no cache configured at all ─────────────────────────────────────────────
    def _missing_cache(
        self, ctx: AuditContext, wf: Workflow, job_key: str, job: Job
    ) -> FindingDraft | None:
        if _job_has_caching(job, ctx.root_package_json):
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


def _job_has_caching(job: Job, package_json: RootPackageJson) -> bool:
    for step in job.steps:
        action = step.action
        if action is None:
            continue
        if action == _CACHE_ACTION or action in _CACHE_EQUIVALENT:
            return True
        if action in _SETUP_ACTIONS and step.with_.get("cache"):
            return True
        if action == _SETUP_NODE and setup_node_auto_caches(job, step, package_json):
            return True
    return False


def setup_node_auto_caches(job: Job, step: Step, package_json: RootPackageJson) -> bool:
    """Does this `actions/setup-node` step cache with no `cache:` input?

    Mirrors setup-node's `src/main.ts`, not its changelog. It caches on its own only when
    all of these hold: v5 or later, `package-manager-cache` not false, and the
    `package.json` at the workspace root names npm. That file exists only once something
    has checked the repository out, so a setup-node before any checkout reads nothing.

    Where we cannot know, the answer is "cached" -- the pre-@3 behaviour -- because this
    decides whether a job is examined at all, and the timing gate is the only check left.
    """
    if step.with_.get("cache"):
        return True
    if str(step.with_.get("package-manager-cache", "")).strip().lower() == "false":
        return False
    major = setup_node_major(step)
    if major is not None and major < _SETUP_NODE_AUTO_CACHE_MAJOR:
        return False
    checkout = _checkout_before(job, step)
    if checkout is None:
        return False  # nothing in the workspace to read yet
    if checkout.with_.get("path") or checkout.with_.get("repository"):
        return True  # it reads a package.json that is not our repo root
    if not package_json.fetched:
        return True
    return package_json.declares_npm()


def setup_node_major(step: Step) -> int | None:
    """`4` from `@v4` or `@v4.1.0`; None for a SHA pin or a branch."""
    ref = (step.uses or "").partition("@")[2].strip()
    m = _MAJOR.match(ref)
    return int(m.group(1)) if m else None


def needs_root_package_json(workflows: list[Workflow]) -> bool:
    """Is the root `package.json` worth an API call? Only if a setup-node step's caching
    turns on it; most repos never pay the request."""
    for wf in workflows:
        if wf.parse_error:
            continue
        for job in wf.jobs.values():
            for step in job.steps:
                if step.action != _SETUP_NODE:
                    continue
                if setup_node_auto_caches(job, step, RootPackageJson(fetched=True)) != \
                        setup_node_auto_caches(job, step, RootPackageJson()):
                    return True
    return False


def _checkout_before(job: Job, step: Step) -> Step | None:
    """The last `actions/checkout` ahead of `step` in the same job."""
    found = None
    for s in job.steps:
        if s is step:
            return found
        if s.action == _CHECKOUT:
            found = s
    return None


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



_NEVER_HITS_TITLE = {
    "run": "Cache key in `{job}` is unique per run — a fresh run never restores it",
    "attempt": (
        "Cache key in `{job}` is unique per attempt — never restored, not even on a re-run"
    ),
    "commit": (
        "Cache key in `{job}` is unique per commit — restored only when that commit is rebuilt"
    ),
}
_SCOPE_RANK = {"attempt": 0, "run": 1, "commit": 2}


def _norm_expr(expr: str) -> str:
    return " ".join(expr.split())


def unique_scope(key: str) -> str | None:
    """The narrowest scope a key is unique at, or None if it can repeat across runs.

    Only whole expressions count. `${{ github.head_ref || github.run_id }}` is per-branch on
    a pull request and would be a false positive to treat as per-run.
    """
    scopes = [
        _UNIQUE_SCOPE[e]
        for e in (_norm_expr(m) for m in _EXPR.findall(key))
        if e in _UNIQUE_SCOPE
    ]
    return min(scopes, key=_SCOPE_RANK.__getitem__) if scopes else None


def _key_pattern(key: str, *, prefix: bool = False) -> re.Pattern[str]:
    """A key as a pattern: per-run expressions stay literal, every other expression
    (`matrix.arch`, `hashFiles(...)`, `runner.os`) matches anything.

    Wildcarding errs toward *finding a restorer*, which silences the finding -- the right
    direction for a rule that claims a cache is dead.
    """
    parts: list[str] = []
    pos = 0
    for m in _EXPR.finditer(key):
        parts.append(re.escape(key[pos:m.start()]))
        expr = _norm_expr(m.group(1))
        parts.append(re.escape(f"${{{{ {expr} }}}}") if expr in _UNIQUE_SCOPE else ".*")
        pos = m.end()
    parts.append(re.escape(key[pos:]))
    return re.compile("".join(parts) + (".*" if prefix else ""), re.S)


def _canonical(key: str) -> str:
    return _EXPR.sub(lambda m: f"${{{{ {_norm_expr(m.group(1))} }}}}", key.strip())


def _key_matches(a: str, b: str) -> bool:
    """Could lookup key `a` hit an entry saved under `b`? Symmetric: either side may carry
    the matrix value literally (`dev-image-amd64-…` against `…-${{ matrix.arch }}-…`)."""
    a, b = a.strip(), b.strip()
    if not a or not b:
        return False
    return bool(
        _key_pattern(a).fullmatch(_canonical(b)) or _key_pattern(b).fullmatch(_canonical(a))
    )


def _restores(other: Step, step: Step, key: str) -> bool:
    """Does `other` read back what `step` saves under `key`? By exact key (a different
    step), or by any of its `restore-keys` prefixes (itself included)."""
    if other is not step and _key_matches(str(other.with_.get("key", "")), key):
        return True
    return any(_key_pattern(p, prefix=True).fullmatch(_canonical(key))
               for p in _restore_keys(other))


def _restore_keys(step: Step) -> list[str]:
    raw = step.with_.get("restore-keys")
    if raw is None:
        return []
    lines = raw if isinstance(raw, list) else str(raw).splitlines()
    return [str(line).strip() for line in lines if str(line).strip()]
