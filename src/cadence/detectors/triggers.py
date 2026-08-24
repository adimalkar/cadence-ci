"""Class C — workflows that run on changes they cannot possibly be affected by.

A docs-only commit that triggers the full integration suite is pure waste, and unlike
most findings the fix is a `paths-ignore:` block that changes no build semantics.

The evidence bar here is the interesting part. Claiming "this workflow did not need to
run" requires knowing what the workflow *depends on*, which static analysis cannot fully
determine. So the rule is inverted: rather than guessing what is safe to skip, it looks
for runs where **every changed file** fell inside a set of paths that are inert by
construction — documentation, markdown, license and editor metadata. Anything outside
that set, and the run is treated as justified.
"""

from __future__ import annotations

import re

from cadence.detectors.base import EvidenceDraft, FindingDraft
from cadence.detectors.context import AuditContext
from cadence.simulate import Savings, SavingsBasis
from cadence.workflow import Workflow

DETECTOR_ID = "waste.irrelevant_path_trigger"
DETECTOR_VERSION = "irrelevant_path_trigger@1"

MIN_RUNS = 30
# At least this share of runs must be provably inert before it is worth a maintainer's
# attention -- a single docs commit triggering CI is noise, not a pattern.
MIN_WASTED_FRACTION = 0.08

# Deliberately conservative. A change under any of these cannot alter build or test
# behaviour for any language we support. Notably absent: config files, CI files, and
# anything under scripts/ -- all of which genuinely can.
_INERT = re.compile(
    r"""^(
        docs?/ | doc/ | website/ | \.github/ISSUE_TEMPLATE/ |
        [^/]*\.(md|rst|txt|adoc)$ |
        (^|.*/)(LICENSE|NOTICE|AUTHORS|CONTRIBUTORS|CHANGELOG|CODEOWNERS)(\.[a-z]+)?$ |
        \.editorconfig$ | \.gitattributes$ | \.gitignore$ |
        (^|.*/)\.?(images?|screenshots?|assets/docs)/
    )""",
    re.IGNORECASE | re.VERBOSE,
)


def is_inert(path: str) -> bool:
    return bool(_INERT.match(path))


class IrrelevantPathTriggerDetector:
    id = DETECTOR_ID
    version = DETECTOR_VERSION

    def run(self, ctx: AuditContext) -> list[FindingDraft]:
        if not ctx.changed_paths:
            return []
        drafts: list[FindingDraft] = []
        for wf in ctx.workflows:
            if wf.parse_error or not wf.jobs:
                continue
            if _already_filtered(wf):
                continue
            draft = self._for_workflow(ctx, wf)
            if draft is not None:
                drafts.append(draft)
        return drafts

    def _for_workflow(self, ctx: AuditContext, wf: Workflow) -> FindingDraft | None:
        wf_runs = [r for r in ctx.runs if r.workflow_path == wf.path]
        known = [r for r in wf_runs if ctx.changed_paths.get(r.run_id)]
        if len(known) < MIN_RUNS:
            return None

        wasted = []
        for run in known:
            paths = ctx.changed_paths[run.run_id]
            if paths and all(is_inert(p) for p in paths):
                wasted.append(run)

        if len(wasted) / len(known) < MIN_WASTED_FRACTION:
            return None

        # Exact replay: these runs would not have started at all.
        total = sum(r.wall_seconds for r in wasted)
        per_run = total / len(known)
        if per_run < 5.0:
            return None

        avg_jobs = _avg_jobs(wasted)
        savings = Savings(
            seconds_per_run=per_run,
            basis=SavingsBasis.REPLAY,
            low=per_run,
            high=per_run,
            n_runs=len(known),
            detail=(
                f"{len(wasted)} of {len(known)} runs changed only inert files "
                f"(docs, markdown, license); those runs would not have started"
            ),
        )
        sample = sorted({p for r in wasted for p in ctx.changed_paths[r.run_id]})[:12]

        return FindingDraft(
            kind="irrelevant_path_trigger",
            module="waste",
            severity=3,
            confidence=0.85,
            dedupe_key=f"irrelevant_path_trigger:{wf.path}",
            title=(
                f"{len(wasted)} of {len(known)} runs of {wf.path.split('/')[-1]} "
                f"were triggered by docs-only changes"
            ),
            detector_version=DETECTOR_VERSION,
            suggested_action=(
                "Add a paths-ignore filter to this workflow's triggers:\n"
                "on:\n  pull_request:\n    paths-ignore: ['docs/**', '**/*.md', 'LICENSE']"
            ),
            savings=savings,
            parallel_jobs=avg_jobs,
            evidence=[
                EvidenceDraft(
                    kind="code_range", file_path=wf.path, line_start=1, line_end=1,
                    payload={"missing": "on.<event>.paths-ignore"},
                ),
                EvidenceDraft(
                    kind="run_history",
                    run_ids=[r.run_id for r in wasted][:50],
                    payload={
                        "wasted_runs": len(wasted), "runs_examined": len(known),
                        "example_paths": sample,
                    },
                ),
                EvidenceDraft(
                    kind="counterfactual",
                    payload={
                        "basis": savings.basis.value,
                        "seconds_per_run": round(per_run, 2),
                        "method": "runs whose entire changeset was inert would not start",
                    },
                ),
            ],
        )


def _already_filtered(wf: Workflow) -> bool:
    """Whether the workflow already declares any path filtering.

    Any filter at all suppresses the finding: the maintainer has clearly thought about
    it, and second-guessing a hand-tuned filter with a generic suggestion is exactly the
    kind of noise that gets a bot uninstalled.
    """
    on = wf.on
    if isinstance(on, dict):
        for spec in on.values():
            if isinstance(spec, dict) and (
                spec.get("paths") or spec.get("paths-ignore")
            ):
                return True
    return False


def _avg_jobs(runs) -> float:
    counts = [len(r.timings) for r in runs if r.timings]
    return (sum(counts) / len(counts)) if counts else 1.0
