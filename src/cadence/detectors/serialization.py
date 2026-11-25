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
DETECTOR_VERSION = "false_needs_edge@2"

# Any action that fetches an artifact, not only actions/download-artifact: forks and
# third-party ones exist, and a missed consumer is a broken build.
_DOWNLOAD = re.compile(r"download[-_]?(?:workflow[-_]?)?artifacts?|artifact[-_]?download", re.I)
_DOWNLOAD_COMMAND = re.compile(r"\bgh\s+run\s+download\b")
_CACHE_SAVE = "actions/cache/save"
_CACHE_RESTORE = "actions/cache/restore"

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

    # 3. A reusable workflow's steps live in another file. Whatever it uploads, downloads
    #    or writes is unreadable from here, on either side of the edge.
    if job.is_reusable_call or dep.is_reusable_call:
        return Verdict(False)

    # 4. Artifact flow. Whether the dependency uploads is not decidable: a third-party
    #    action can upload without saying so (`hynek/build-and-inspect-python-package`),
    #    and so can a script. So a job that downloads anything is assumed to consume it.
    if _downloads_artifact(job):
        return Verdict(False)
    #    Splitting save from restore is how a cache is used to hand files to a later job.
    if any(st.action == _CACHE_RESTORE for st in job.steps) or any(
        st.action == _CACHE_SAVE for st in dep.steps
    ):
        return Verdict(False)

    # 5. Shared external state we cannot model: service containers, deployment
    #    environments, or concurrency groups that may serialise them anyway.
    if job.services or dep.services:
        return Verdict(False)
    if job.raw.get("environment") or dep.raw.get("environment"):
        return Verdict(False)
    if job.raw.get("concurrency") or dep.raw.get("concurrency"):
        return Verdict(False)

    # 6. A dependency that only gates on success is still doing a job -- it is a cheap
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

    # 0.5, not 0.8: every public finding of @1 was wrong (CAVEATS 92), and after those
    # fixes the corpus has none left to measure against.
    return Verdict(
        True, confidence=0.50,
        reason="no artifact download, no output reference, no shared services or environment",
    )


def _downloads_artifact(job: Job) -> bool:
    for step in job.steps:
        if step.uses and _DOWNLOAD.search(step.uses):
            return True
        if step.run and _DOWNLOAD_COMMAND.search(step.run):
            return True
    return False


def _references_needs(job: Job, dep_key: str) -> bool:
    """Search the whole job body for a use of `needs` that could read `dep_key`.

    Serialising to JSON catches references anywhere -- `if:`, `env:`, `with:`, a step's
    `run:` body -- without hand-walking every construct GitHub supports. The job's own
    `needs:` list is dropped first, being the edge under test rather than a use of it.

    `needs.<other>` reads only that other job. Every other form -- `toJSON(needs)`,
    `needs.*.result`, `needs[...]` -- can read them all, and is how aggregate "all checks
    passed" jobs consume the jobs they wait for.
    """
    try:
        body = {k: v for k, v in job.raw.items() if k != "needs"}
        blob = json.dumps(body, default=str)
    except (TypeError, ValueError):
        return True  # unreadable -> assume coupled
    for match in re.finditer(r"(?<![\w.-])needs\b(\s*\.\s*([\w-]+|\*))?", blob, re.I):
        named = match.group(2)
        if named is None or named == "*" or named.lower() == dep_key.lower():
            return True
    return False


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
