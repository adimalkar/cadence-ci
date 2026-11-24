"""The analysis contract: one versioned, read-only result that every adapter shares.

`cadence analyze` (and, next, an MCP server) call `analyze` and render one of two views:

* **summary** -- what an agent needs to decide what to look at: freshness, coverage, the
  ranked findings with their savings basis and suppression state, and evidence *handles*
  but not evidence bodies. Small on purpose: it replaces paging through `gh run list`,
  job listings and logs, which costs tens of thousands of tokens for a single repo.
* **finding** -- one finding in full: its suggested action and every evidence item,
  bounded, with the exact source SHA its line references point into.

Three properties are the point of the module, not incidental to it:

1. **Read-only.** The database transaction is `READ ONLY`, so a code path that tried to
   write would fail rather than quietly persist. Nothing is stored -- unlike `audit
   --dry-run`, which still writes a workflow snapshot.
2. **Pinned.** Analysis runs against a `PreviewSource`: workflow files, root
   `package.json`, local actions and `.cadenceignore`, all read at one commit SHA. The same
   source plus the same stored history gives the same `analysis_id`, so a follow-up call
   can say whether it is looking at the analysis the agent already saw.
3. **Honest about absence.** Runs analysed, how many carry step timings, how many have
   changed-path data, which detectors failed -- reported, so "no findings" can be told
   apart from "no data".
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from cadence.audit import DETECTORS, build_context, run_audit, summarize_pipeline
from cadence.detectors.base import Detector, EvidenceDraft, FindingDraft
from cadence.source import PreviewSource
from cadence.suppress import SuppressionError, collect, stored_matches

SCHEMA_VERSION = "cadence.analysis/1"
RUN_URL = "https://github.com/{repo}/actions/runs/{run_id}"
# Bounds on what a single view returns. Evidence lists can run to hundreds of run ids or
# durations; a reader needs a sample and the true count, not all of them.
MAX_LIST_ITEMS = 50
SUMMARY_RUN_SAMPLE = 3


class AnalysisError(Exception):
    """A refusal with a machine-readable code, so a caller can branch on it."""

    def __init__(self, code: str, message: str, *, hint: str | None = None,
                 retryable: bool = False) -> None:
        super().__init__(message)
        self.code, self.message, self.hint, self.retryable = code, message, hint, retryable

    def as_dict(self) -> dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION,
                "error": {"code": self.code, "message": self.message, "hint": self.hint,
                          "retryable": self.retryable}}


def finding_id(repo: str, dedupe_key: str) -> str:
    """Stable across analyses: the same finding keeps its id while its numbers change.

    Not the database `finding.id`, which exists only for persisted findings; this one
    exists for every analysis, including ones that never write.
    """
    return "f_" + hashlib.sha256(f"{repo}\0{dedupe_key}".encode()).hexdigest()[:12]


@dataclass(slots=True)
class Analysis:
    repo: str
    source: PreviewSource
    source_path: str | None
    analyzed_at: datetime
    window_days: int
    limit_runs: int
    run_ids: list[int]
    coverage: dict[str, Any]
    pipeline: dict[str, Any] | None
    findings: list[FindingDraft]
    suppression: dict[str, dict[str, Any]]  # dedupe_key -> {reason, scope, source}
    detectors: list[dict[str, Any]]
    warnings: list[str]

    @property
    def analysis_id(self) -> str:
        basis = json.dumps({
            "repo": self.repo, "sha": self.source.inventory.ref,
            "window_days": self.window_days, "limit_runs": self.limit_runs,
            "runs": sorted(self.run_ids),
            "detectors": sorted(f"{d['id']}@{d['version']}" for d in self.detectors),
        }, sort_keys=True)
        return "a_" + hashlib.sha256(basis.encode()).hexdigest()[:16]


def analyze(conn, source: PreviewSource, *, window_days: int = 90, limit_runs: int = 500,
            source_path: str | None = None, now: datetime | None = None) -> Analysis:
    """Run every detector against stored history and a pinned source. Writes nothing.

    `conn` must not be inside a transaction: the first statement makes it read-only.
    """
    conn.execute("SET TRANSACTION READ ONLY")
    owner, name = source.repo.split("/", 1)
    row = conn.execute("SELECT id FROM repo WHERE owner = %s AND name = %s",
                       (owner, name)).fetchone()
    if row is None:
        raise AnalysisError("not_ingested", f"{source.repo} has no stored history",
                            hint=f"cadence ingest {source.repo}")
    repo_id = row["id"]

    ctx = build_context(conn, repo_id, source.workflows, window_days=window_days,
                        limit_runs=limit_runs, root_package_json=source.package_json,
                        actions=source.actions)
    if not ctx.runs:
        raise AnalysisError(
            "no_runs", f"no stored runs with job timings in the last {window_days} days",
            hint=f"cadence ingest {source.repo}")

    result = run_audit(conn, ctx, commit_sha=source.inventory.ref, persist=False)
    warnings: list[str] = []

    suppression: dict[str, dict[str, Any]] = {}
    try:
        rules = collect(source.workflows, source.ignore_file)
    except SuppressionError as exc:
        # Shown, not hidden: a broken policy must not silence findings it cannot express.
        warnings.append(f"repository suppression policy ignored: {exc}")
        rules = []
    stored = conn.execute(
        "SELECT kind, dedupe_key, suppress_scope, suppressed_reason, suppress_source"
        " FROM finding WHERE repo_id = %s AND status = 'suppressed'", (repo_id,),
    ).fetchall()
    for d in result["drafts"]:
        rule = next((r for r in rules if r.matches(d.kind, d.dedupe_key)), None)
        if rule is not None:
            suppression[d.dedupe_key] = {"reason": rule.reason, "scope": rule.scope,
                                         "source": rule.source}
            continue
        hit = next((s for s in stored if stored_matches(s, d.kind, d.dedupe_key)), None)
        if hit is not None:
            suppression[d.dedupe_key] = {"reason": hit["suppressed_reason"],
                                         "scope": hit["suppress_scope"],
                                         "source": hit["suppress_source"]}

    coverage = _coverage(conn, repo_id, ctx, window_days, limit_runs)
    if coverage["runs_with_step_timings"] < 0.9 * coverage["runs_with_jobs"]:
        warnings.append("fewer than 90% of runs carry step timings; step-based rules "
                        "(long tail, caching, first failing step) see only those runs")
    if coverage["run_limit_reached"]:
        warnings.append(f"the {limit_runs}-run limit was reached before the window "
                        f"start; history analysed begins at {coverage['first_run_at']}")
    failed = dict(result["failed"])
    registered: list[Detector] = list(DETECTORS)  # type: ignore[arg-type]
    detectors = [{"id": d.id, "version": d.version,
                  "status": "failed" if d.id in failed else "ok",
                  **({"error": failed[d.id]} if d.id in failed else {})}
                 for d in registered]
    if failed:
        warnings.append(f"{len(failed)} detector(s) failed; their silence is not a result")

    return Analysis(
        repo=source.repo, source=source, source_path=source_path,
        analyzed_at=now or datetime.now(UTC), window_days=window_days,
        limit_runs=limit_runs, run_ids=[r.run_id for r in ctx.runs], coverage=coverage,
        pipeline=summarize_pipeline(ctx), findings=result["drafts"],
        suppression=suppression, detectors=detectors, warnings=warnings,
    )


def _coverage(conn, repo_id: int, ctx, window_days: int, limit_runs: int) -> dict[str, Any]:
    run_ids = [r.run_id for r in ctx.runs]
    with_steps = conn.execute(
        "SELECT count(DISTINCT j.run_id) AS n FROM job j WHERE j.run_id = ANY(%s)"
        " AND EXISTS (SELECT 1 FROM step s WHERE s.job_id = j.id)", (run_ids,),
    ).fetchone()["n"]
    window = conn.execute(
        """
        SELECT count(*) AS n, min(created_at) AS first, max(created_at) AS last
        FROM (SELECT created_at FROM run
              WHERE repo_id = %s AND created_at > now() - make_interval(days => %s)
              ORDER BY created_at DESC LIMIT %s) w
        """,
        (repo_id, window_days, limit_runs),
    ).fetchone()
    latest = conn.execute("SELECT max(created_at) AS t FROM run WHERE repo_id = %s",
                          (repo_id,)).fetchone()["t"]
    return {
        "runs_in_window": window["n"],
        "runs_with_jobs": len(run_ids),
        "runs_with_step_timings": with_steps,
        "runs_with_changed_paths": len(ctx.changed_paths),
        "first_run_at": _iso(window["first"]),
        "last_run_at": _iso(window["last"]),
        "latest_stored_run_at": _iso(latest),
        "run_limit_reached": window["n"] >= limit_runs,
    }


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if value else None


# ──────────────────────────────────────────────────────────────────── views


def _header(a: Analysis) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "analysis_id": a.analysis_id,
        "repo": a.repo,
        "source": {"sha": a.source.inventory.ref,
                   "workflows": sorted(a.source.workflows),
                   "snapshot": a.source_path},
        "analyzed_at": _iso(a.analyzed_at),
        "window": {"days": a.window_days, "run_limit": a.limit_runs},
        "coverage": a.coverage,
        "read_only": True,
    }


def _savings(d: FindingDraft) -> dict[str, Any] | None:
    s = d.savings
    if s is None:
        return None
    return {
        "basis": s.basis.value,  # replay: measured; projection: estimated range
        "seconds_per_run": round(s.seconds_per_run, 1),
        "low": round(s.low, 1), "high": round(s.high, 1),
        "n_runs": s.n_runs,
        # Wall-clock seconds are what a developer waits; billed seconds multiply by the
        # jobs that stop running in parallel. Stated separately, never conflated.
        "billed_seconds_per_run": round(s.seconds_per_run * d.parallel_jobs, 1),
    }


def _handle(fid: str, i: int, e: EvidenceDraft, sha: str) -> dict[str, Any]:
    h: dict[str, Any] = {"id": f"{fid}.e{i}", "kind": e.kind}
    if e.file_path:
        h["file"] = e.file_path
        if e.line_start:
            h["lines"] = [e.line_start, e.line_end or e.line_start]
            h["at"] = sha
    if e.run_ids:
        h["runs"] = len(e.run_ids)
    return h


def summary_view(a: Analysis, *, top: int = 10, include_suppressed: bool = False
                 ) -> dict[str, Any]:
    sha = a.source.inventory.ref
    rows = []
    hidden = 0
    for d in a.findings:
        sup = a.suppression.get(d.dedupe_key)
        if sup and not include_suppressed:
            hidden += 1
            continue
        fid = finding_id(a.repo, d.dedupe_key)
        row: dict[str, Any] = {
            "id": fid, "kind": d.kind, "module": d.module, "severity": d.severity,
            "confidence": d.confidence, "title": d.title, "savings": _savings(d),
            "evidence": [_handle(fid, i, e, sha) for i, e in enumerate(d.evidence)],
        }
        if sup:
            row["suppressed"] = sup
        rows.append(row)
    shown = rows[:top]
    replay = sum(d.savings.seconds_per_run for d in a.findings
                 if d.savings and d.savings.basis.is_replay
                 and d.dedupe_key not in a.suppression)
    pipeline = None
    if a.pipeline:
        p = a.pipeline
        pipeline = {k: (round(v, 1) if isinstance(v, float) else v) for k, v in p.items()
                    if k in ("workflow", "runs", "jobs", "wall_seconds", "critical_path_seconds",
                             "floor_seconds", "queue_seconds", "queue_bound")}
        pipeline["job_mapping_coverage"] = round(p.get("coverage", 0.0), 3)
    return {
        **_header(a),
        "pipeline": pipeline,
        "totals": {"findings": len(a.findings), "shown": len(shown),
                   "suppressed_hidden": hidden, "more": max(0, len(rows) - len(shown)),
                   "measured_replay_seconds_per_run": round(replay, 1)},
        "findings": shown,
        "detectors_failed": [d for d in a.detectors if d["status"] == "failed"],
        "warnings": a.warnings,
        "next": ("cadence analyze --snapshot <source.snapshot> --finding <id> returns one "
                 "finding's suggested action and full evidence, offline"),
    }


def _bounded(value: Any) -> Any:
    if isinstance(value, list):
        if len(value) > MAX_LIST_ITEMS:
            return {"first": [_bounded(v) for v in value[:MAX_LIST_ITEMS]],
                    "total": len(value)}
        return [_bounded(v) for v in value]
    if isinstance(value, dict):
        return {k: _bounded(v) for k, v in value.items()}
    if isinstance(value, float):
        return round(value, 2)
    return value


def finding_view(a: Analysis, fid: str) -> dict[str, Any]:
    d = next((d for d in a.findings if finding_id(a.repo, d.dedupe_key) == fid), None)
    if d is None:
        raise AnalysisError(
            "finding_not_found",
            f"{fid} is not in analysis {a.analysis_id}",
            hint="the finding may have resolved since; re-run cadence analyze for current ids")
    sha = a.source.inventory.ref
    evidence = []
    for i, e in enumerate(d.evidence):
        item = _handle(fid, i, e, sha)
        if e.run_ids:
            item["run_ids"] = _bounded(list(e.run_ids))
        if e.payload:
            item["payload"] = _bounded(e.payload)
        evidence.append(item)
    return {
        **_header(a),
        "finding": {
            "id": fid, "kind": d.kind, "module": d.module, "severity": d.severity,
            "confidence": d.confidence, "title": d.title,
            "detector_version": d.detector_version, "dedupe_key": d.dedupe_key,
            "suggested_action": d.suggested_action, "savings": _savings(d),
            "savings_detail": d.savings.detail if d.savings else None,
            "suppressed": a.suppression.get(d.dedupe_key),
            "evidence": evidence,
        },
        "run_url": RUN_URL.replace("{repo}", a.repo),
        "warnings": a.warnings,
    }
