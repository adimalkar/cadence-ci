"""Audit orchestration: assemble context from stored history, run detectors, persist.

The join that makes this work is config ↔ observed timings, and it hinges on `name_base`
— the verbatim-vs-stripped job name split added by the Phase 0 audit. A workflow's job
*key* is not its runtime *name*, and matrix legs append a suffix at runtime, so matching
raw names would silently miss every matrix job.
"""

from __future__ import annotations

import structlog

from cadence.config import settings
from cadence.cost import CostContext, load_rate_card
from cadence.dag import aggregate_spans, critical_path, theoretical_floor
from cadence.detectors.cache import DependencyCacheDetector
from cadence.detectors.cancellation import NoRunCancellationDetector
from cadence.detectors.context import AuditContext, RunObservation, StepSeries
from cadence.detectors.longtail import LongTailStepDetector
from cadence.detectors.matrix import NonDiscriminatingMatrixLegDetector
from cadence.detectors.serialization import FalseNeedsEdgeDetector
from cadence.detectors.triggers import IrrelevantPathTriggerDetector
from cadence.findings import persist_findings, resolve_missing
from cadence.workflow import Workflow, parse_workflow

log = structlog.get_logger(__name__)

DETECTORS = [
    NoRunCancellationDetector(),
    FalseNeedsEdgeDetector(),
    DependencyCacheDetector(),
    NonDiscriminatingMatrixLegDetector(),
    IrrelevantPathTriggerDetector(),
    LongTailStepDetector(),
]


def build_context(
    conn,
    repo_id: int,
    workflow_files: dict[str, str],
    *,
    window_days: int = 90,
    limit_runs: int = 200,
) -> AuditContext:
    from psycopg.rows import dict_row

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT owner, name, is_private FROM repo WHERE id = %s", (repo_id,)
        )
        repo = cur.fetchone()
        if repo is None:
            raise ValueError(f"repo {repo_id} not ingested")

        cur.execute(
            """
            SELECT id, head_branch, conclusion, workflow_path, head_sha,
                   extract(epoch FROM created_at) AS created_epoch
            FROM run
            WHERE repo_id = %s AND created_at > now() - make_interval(days => %s)
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (repo_id, window_days, limit_runs),
        )
        run_rows = cur.fetchall()
        run_ids = [r["id"] for r in run_rows]

        jobs_by_run: dict[int, list[dict]] = {}
        if run_ids:
            cur.execute(
                """
                SELECT run_id, name, name_base,
                       extract(epoch FROM created_at) AS created_epoch,
                       extract(epoch FROM started_at) AS started_epoch,
                       extract(epoch FROM completed_at) AS completed_epoch
                FROM job
                WHERE run_id = ANY(%s)
                  AND started_at IS NOT NULL AND completed_at IS NOT NULL
                """,
                (run_ids,),
            )
            for row in cur.fetchall():
                jobs_by_run.setdefault(row["run_id"], []).append(row)

        # Step duration history, keyed by (job name_base, step name), for cache analysis.
        step_series: dict[tuple[str, str], StepSeries] = {}
        if run_ids:
            cur.execute(
                """
                SELECT j.name_base, s.name AS step_name, j.run_id,
                       extract(epoch FROM (s.completed_at - s.started_at)) AS dur
                FROM step s JOIN job j ON j.id = s.job_id
                WHERE j.run_id = ANY(%s)
                  AND s.started_at IS NOT NULL AND s.completed_at IS NOT NULL
                  AND s.conclusion = 'success'
                """,
                (run_ids,),
            )
            for row in cur.fetchall():
                key = (row["name_base"], row["step_name"])
                series = step_series.get(key)
                if series is None:
                    series = StepSeries(job_key=row["name_base"], step_name=row["step_name"])
                    step_series[key] = series
                if row["dur"] is not None:
                    series.durations.append(float(row["dur"]))
                    series.run_ids.append(row["run_id"])

        # Per-leg outcomes and durations for the matrix rule. Keyed on the verbatim
        # name, since that is exactly what distinguishes one leg from another.
        leg_outcomes: dict[str, dict[str, list[tuple[int, str | None]]]] = {}
        leg_durations: dict[tuple[str, str], list[float]] = {}
        if run_ids:
            cur.execute(
                """
                SELECT r.workflow_path, j.name, j.run_id, j.conclusion,
                       extract(epoch FROM (j.completed_at - j.started_at)) AS exec_s
                FROM job j JOIN run r ON r.id = j.run_id
                WHERE j.run_id = ANY(%s) AND r.workflow_path IS NOT NULL
                """,
                (run_ids,),
            )
            for row in cur.fetchall():
                wf_path, leg = row["workflow_path"], row["name"]
                leg_outcomes.setdefault(wf_path, {}).setdefault(leg, []).append(
                    (row["run_id"], row["conclusion"])
                )
                if row["exec_s"] is not None:
                    leg_durations.setdefault((wf_path, leg), []).append(float(row["exec_s"]))

    workflows = [parse_workflow(path, content) for path, content in workflow_files.items()]
    runs = _observations(run_rows, jobs_by_run, workflows)

    rate_card = load_rate_card(conn, settings.rate_card_version)
    span_days = max(1.0, float(window_days))
    cost = CostContext(
        is_private=repo["is_private"],
        runs_per_month=(len(run_rows) / span_days) * 30.0,
        rate_card=rate_card,
        dominant_labels=_dominant_labels(conn, repo_id),
    )

    return AuditContext(
        repo_id=repo_id,
        owner=repo["owner"],
        name=repo["name"],
        is_private=repo["is_private"],
        workflows=workflows,
        runs=runs,
        step_series=step_series,
        cost=cost,
        window_days=window_days,
        leg_outcomes=leg_outcomes,
        leg_durations=leg_durations,
    )


def _observations(run_rows, jobs_by_run, workflows: list[Workflow]) -> list[RunObservation]:
    """Map observed jobs onto config job keys, collapsing matrix legs."""
    out: list[RunObservation] = []
    for row in run_rows:
        job_rows = jobs_by_run.get(row["id"], [])
        if not job_rows:
            continue

        spans: list[tuple[str, float | None, float | None, float | None]] = []
        for jr in job_rows:
            key = _config_key_for(jr["name"], jr["name_base"], workflows)
            if key is None:
                continue
            spans.append(
                (key, jr["created_epoch"], jr["started_epoch"], jr["completed_epoch"])
            )

        starts = [float(j["created_epoch"]) for j in job_rows if j["created_epoch"]]
        ends = [float(j["completed_epoch"]) for j in job_rows if j["completed_epoch"]]
        out.append(
            RunObservation(
                run_id=row["id"],
                head_branch=row["head_branch"],
                started_epoch=min(starts) if starts else None,
                completed_epoch=max(ends) if ends else None,
                conclusion=row["conclusion"],
                workflow_path=row["workflow_path"],
                head_sha=row["head_sha"],
                jobs_total=len(job_rows),
                jobs_mapped=len(spans),
                timings=aggregate_spans(spans),
            )
        )
    return out


def _config_key_for(
    name: str | None, name_base: str | None, workflows: list[Workflow]
) -> str | None:
    """Resolve an observed job to a config job key, verbatim name first.

    Ruff-style names like `cargo test (linux)` are hand-written but indistinguishable
    from a matrix suffix once stripped, so matching on `name_base` alone maps almost
    nothing -- which silently collapses the DAG to a single node and voids critical-path
    analysis.
    """
    if not name and not name_base:
        return None
    for wf in workflows:
        job = wf.job_for_runtime_name(name, name_base)
        if job is not None:
            return job.key
    return None


def _dominant_labels(conn, repo_id: int) -> list[str]:
    from psycopg.rows import dict_row

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT unnest(runner_labels) AS label, count(*) AS n
            FROM job WHERE repo_id = %s AND runner_labels <> '{}'
            GROUP BY 1 ORDER BY n DESC LIMIT 1
            """,
            (repo_id,),
        )
        row = cur.fetchone()
    return [row["label"]] if row else ["ubuntu-latest"]


async def enrich_changed_paths(provider, repo, ctx: AuditContext, *, max_runs: int = 150) -> int:
    """Populate `ctx.changed_paths` for the path-trigger rule.

    One API request per distinct commit, so it is capped and opt-in. Without this the
    trigger rule has no data and stays silent -- which is correct behaviour, but means the
    rule only ever fires when a caller has paid for the enrichment.
    """
    seen: dict[str, list[str]] = {}
    ordered = [r for r in ctx.runs if r.head_sha][:max_runs]
    for run in ordered:
        sha = run.head_sha
        if sha not in seen:
            try:
                seen[sha] = await provider.fetch_commit_paths(repo, sha)
            except Exception:  # enrichment is best-effort; never fail the audit for it
                seen[sha] = []
        if seen[sha]:
            ctx.changed_paths[run.run_id] = seen[sha]
    return len(ctx.changed_paths)


def run_audit(conn, ctx: AuditContext, *, commit_sha: str, persist: bool = True) -> dict:
    drafts = []
    failed: list[tuple[str, str]] = []
    for detector in DETECTORS:
        try:
            found = detector.run(ctx)
            drafts.extend(found)
        except Exception as exc:  # one bad detector must not void the whole audit
            # Reported to the caller, not just logged: a silently disabled rule looks
            # identical to a repo with nothing to find, which is how a broken detector
            # survives a whole corpus sweep unnoticed.
            failed.append((detector.id, str(exc)[:200]))
            log.warning("audit.detector_failed", detector=detector.id, error=str(exc))

    drafts.sort(
        key=lambda d: (d.savings.seconds_per_run if d.savings else 0.0), reverse=True
    )

    result = {"drafts": drafts, "persisted": None, "resolved": 0, "failed": failed}
    if persist and drafts:
        result["persisted"] = persist_findings(
            conn, ctx.repo_id, drafts, commit_sha=commit_sha, cost=ctx.cost
        )
        result["resolved"] = resolve_missing(
            conn, ctx.repo_id, {d.dedupe_key for d in drafts}, commit_sha=commit_sha
        )
    return result


def summarize_pipeline(ctx: AuditContext) -> dict | None:
    """Wall-clock vs critical path vs floor — the frame every finding is read against.

    Scoped to a **single workflow**, deliberately. A repo's runs span many workflows with
    wildly different shapes (ruff: 37.7 jobs on ci.yaml, 1.0 on typing_conformance), so a
    median across all of them describes no real pipeline. The workflow reported is the one
    that dominates elapsed time — the thing a contributor actually waits on.
    """
    scored = [r for r in ctx.runs if r.timings and r.wall_seconds > 0]
    if not scored:
        return None

    by_workflow: dict[str | None, list] = {}
    for run in scored:
        by_workflow.setdefault(run.workflow_path, []).append(run)

    path = max(by_workflow, key=lambda p: sum(r.wall_seconds for r in by_workflow[p]))
    runs = sorted(by_workflow[path], key=lambda r: r.wall_seconds)
    median = runs[len(runs) // 2]

    wf = next((w for w in ctx.workflows if w.path == path and not w.parse_error), None)
    cp = critical_path(ctx.edges_for(wf), median.timings) if wf and wf.jobs else None

    total_queue = sum(t.queue_seconds for t in median.timings.values())
    total_exec = sum(t.exec_seconds for t in median.timings.values())

    coverage = (
        sum(r.jobs_mapped for r in runs) / sum(r.jobs_total for r in runs)
        if sum(r.jobs_total for r in runs) else 0.0
    )

    return {
        "workflow": path,
        "runs": len(runs),
        "coverage": coverage,
        "wall_seconds": median.wall_seconds,
        "critical_path_seconds": cp.total_seconds if cp else None,
        "critical_path": cp.path if cp else [],
        "floor_seconds": theoretical_floor(median.timings),
        "jobs": len(median.timings),
        "queue_seconds": total_queue,
        "exec_seconds": total_exec,
        # More parallelism makes a queue-bound pipeline slower, not faster. Every other
        # tool's advice is "parallelise more"; saying the opposite requires measuring it.
        "queue_bound": total_queue > total_exec,
    }
