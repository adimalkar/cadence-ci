"""False `needs:` edges — jobs serialised behind work they never consume.

This is the highest-risk rule in the catalog. Every other finding wastes someone's time
if wrong; this one breaks their build. The design constraint from PHASE_1_WASTE_AUDIT.md
is therefore inverted relative to the other detectors:

    Require *positive evidence of independence*, not merely the absence of evidence of
    dependence.

Concretely: silence about a dependency is not proof there isn't one. Anything we cannot
read — a shared service container, a deployment environment, a downloaded artifact by
wildcard — suppresses the finding rather than lowering its confidence.
"""

from __future__ import annotations

import json
import re

from cadence.dag import NodeTiming
from cadence.detectors.base import EvidenceDraft, FindingDraft
from cadence.detectors.context import AuditContext
from cadence.simulate import replay_edge_removal_savings
from cadence.workflow import Job, Workflow

DETECTOR_ID = "waste.false_needs_edge"
DETECTOR_VERSION = "false_needs_edge@1"

_UPLOAD = "actions/upload-artifact"
_DOWNLOAD = "actions/download-artifact"

# Minimum runs before an edge-removal claim is worth making. Below this the replay
# median is dominated by noise in a handful of runs.
MIN_RUNS = 20


class FalseNeedsEdgeDetector:
    id = DETECTOR_ID
    version = DETECTOR_VERSION

    def run(self, ctx: AuditContext) -> list[FindingDraft]:
        drafts: list[FindingDraft] = []
        if len(ctx.runs) < MIN_RUNS:
            return drafts

        for wf in ctx.workflows:
            if wf.parse_error or len(wf.jobs) < 2:
                continue
            drafts.extend(self._for_workflow(ctx, wf))
        return drafts

    def _for_workflow(self, ctx: AuditContext, wf: Workflow) -> list[FindingDraft]:
        out: list[FindingDraft] = []
        edges = ctx.edges_for(wf)

        per_run = [
            (edges, r.timings)
            for r in ctx.runs
            if r.timings and len(r.timings) >= 2
        ]
        if len(per_run) < MIN_RUNS:
            return out

        for job_key, job in wf.jobs.items():
            for dep_key in job.needs:
                dep = wf.jobs.get(dep_key)
                if dep is None:
                    continue

                verdict = independence_verdict(job, dep, dep_key)
                if not verdict.independent:
                    continue

                savings = replay_edge_removal_savings(
                    per_run, from_node=dep_key, to_node=job_key
                )
                # An edge that costs nothing is not worth a maintainer's attention, even
                # if it is genuinely redundant.
                if savings is None or savings.seconds_per_run < 5.0:
                    continue

                out.append(
                    FindingDraft(
                        kind="false_needs_edge",
                        module="waste",
                        severity=3,
                        confidence=verdict.confidence,
                        dedupe_key=f"false_needs_edge:{wf.path}:{job_key}:{dep_key}",
                        title=(
                            f"`{job_key}` waits for `{dep_key}` but consumes nothing "
                            f"from it"
                        ),
                        detector_version=DETECTOR_VERSION,
                        suggested_action=(
                            f"Remove `{dep_key}` from `{job_key}`'s `needs:` in {wf.path}. "
                            f"Verify first: {verdict.reason}"
                        ),
                        savings=savings,
                        parallel_jobs=1.0,  # unblocking one job saves that job's elapsed time
                        evidence=[
                            EvidenceDraft(
                                kind="code_range",
                                file_path=wf.path,
                                line_start=job.line,
                                line_end=job.line,
                                payload={"job": job_key, "needs": dep_key},
                            ),
                            EvidenceDraft(
                                kind="run_history",
                                run_ids=[r.run_id for r in ctx.runs][:50],
                                payload={"detail": savings.detail},
                            ),
                            EvidenceDraft(
                                kind="counterfactual",
                                payload={
                                    "basis": savings.basis.value,
                                    "seconds_per_run": round(savings.seconds_per_run, 2),
                                    "method": "DAG re-levelled with the edge removed",
                                    "independence": verdict.reason,
                                },
                            ),
                        ],
                    )
                )
        return out


class Verdict:
    __slots__ = ("independent", "confidence", "reason")

    def __init__(self, independent: bool, confidence: float = 0.0, reason: str = "") -> None:
        self.independent = independent
        self.confidence = confidence
        self.reason = reason


def independence_verdict(job: Job, dep: Job, dep_key: str) -> Verdict:
    """Decide whether `job` provably consumes nothing from `dep`.

    Returns not-independent whenever anything is unreadable. The asymmetry is the point:
    a missed opportunity costs a maintainer nothing, a wrong removal costs them a broken
    pipeline.
    """
    # 1. Any expression referencing the dependency is a real coupling -- outputs, result,
    #    or anything else under needs.<dep>.
    if _references_needs(job, dep_key):
        return Verdict(False)

    # 2. Declared outputs mean the dependency exists to be consumed, even if this job's
    #    reference is somewhere we cannot see (a composite action, a called script).
    if dep.raw.get("outputs"):
        return Verdict(False)

    # 3. Artifact flow. If the dep uploads and this job downloads *anything*, we cannot
    #    reliably match names (they can be templated or wildcarded) -- assume coupling.
    dep_uploads = any(s.action == _UPLOAD for s in dep.steps)
    job_downloads = any(s.action == _DOWNLOAD for s in job.steps)
    if dep_uploads and job_downloads:
        return Verdict(False)

    # 4. Shared external state we cannot model: service containers, deployment
    #    environments, or concurrency groups that may serialise them anyway.
    if job.services or dep.services:
        return Verdict(False)
    if job.raw.get("environment") or dep.raw.get("environment"):
        return Verdict(False)
    if job.raw.get("concurrency") or dep.raw.get("concurrency"):
        return Verdict(False)

    # 5. A dependency that only gates on success is still doing a job -- it is a cheap
    #    fail-fast guard. Removing it is a policy change, not a pure speedup.
    if _is_cheap_gate(dep):
        return Verdict(
            True,
            confidence=0.60,
            reason=(
                f"`{dep_key}` looks like a fast gate; removing the edge trades "
                f"fail-fast for parallelism"
            ),
        )

    reason = "no artifact flow, no output reference, no shared services or environment"
    if dep_uploads:
        reason += "; dependency uploads artifacts this job never downloads"
    return Verdict(True, confidence=0.80, reason=reason)


def _references_needs(job: Job, dep_key: str) -> bool:
    """Search the whole job body for `needs.<dep>` in any expression.

    Serialising to JSON catches references anywhere -- `if:`, `env:`, `with:`, a step's
    `run:` body -- without hand-walking every construct GitHub supports.
    """
    try:
        blob = json.dumps(job.raw, default=str)
    except (TypeError, ValueError):
        return True  # unreadable -> assume coupled
    pattern = re.compile(r"needs\s*\.\s*" + re.escape(dep_key) + r"\b")
    return bool(pattern.search(blob))


def _is_cheap_gate(dep: Job) -> bool:
    """A short job whose purpose is to fail fast (lint, change detection, guard)."""
    if len(dep.steps) > 4:
        return False
    hints = ("lint", "check", "guard", "detect", "changes", "filter", "gate")
    name = (dep.name or dep.key).lower()
    return any(h in name for h in hints)


def node_timings_from(rows: list[tuple[str, float, float]]) -> dict[str, NodeTiming]:
    from cadence.dag import aggregate_legs

    return aggregate_legs(rows)
