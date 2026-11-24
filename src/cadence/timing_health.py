"""Offline diagnostics for stored observations, never a completeness certificate."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import pairwise

import psycopg
from psycopg.rows import dict_row

from cadence.models import Job, Run, Step

MAX_ID = 2**63 - 1
MAX_JOBS = 10_000
MAX_STEPS = 50_000


@dataclass
class Issues:
    budget: int
    rows: list[dict] = field(default_factory=list)
    codes: Counter[str] = field(default_factory=Counter)
    severities: Counter[str] = field(default_factory=Counter)

    def add(self, code: str, severity: str, **context) -> None:
        self.codes[code] += 1
        self.severities[severity] += 1
        if len(self.rows) < self.budget:
            self.rows.append({"code": code, "severity": severity, **context})


def _times(
    issues: Issues, values: dict[str, datetime | None], *,
    reversed_severity: str = "invalid", **context,
) -> dict[str, datetime]:
    valid = {}
    for name, value in values.items():
        if value is None:
            continue
        if not isinstance(value, datetime) or value.utcoffset() is None:
            issues.add("invalid_timestamp", "invalid", field=name,
                       observed=value.isoformat() if isinstance(value, datetime) else
                       {"type": type(value).__name__}, **context)
        else:
            valid[name] = value
    for (left, start), (right, end) in pairwise(valid.items()):
        if end < start:
            issues.add("reversed_timestamps", reversed_severity, field=right, related_field=left,
                       observed={left: start.isoformat(), right: end.isoformat()}, **context)
    return valid


def inspect_run(run: Run, *, max_issues: int = 200) -> dict:
    """Inspect current-attempt rows; issue totals remain complete when detail is capped."""
    if type(max_issues) is not int or not 0 <= max_issues <= 2000:
        raise ValueError("max-issues must be between 0 and 2000")
    issues = Issues(max_issues)
    if run.status is None:
        issues.add("unknown_run_status", "unknown", field="status", observed=None)
    current = [job for job in run.jobs if job.attempt == run.run_attempt]
    if not current:
        issues.add("no_current_attempt_jobs", "info" if run.conclusion == "skipped"
                   or run.status != "completed" else "unknown", field="jobs")
    for job in sorted(current, key=lambda j: j.id):
        ctx = {"job_id": job.id, "job_status": job.status, "job_conclusion": job.conclusion}
        if job.run_id != run.id:
            issues.add("job_run_mismatch", "invalid", field="run_id", observed=job.run_id, **ctx)
            continue
        times = _times(issues, {"created_at": job.created_at, "started_at": job.started_at,
                               "completed_at": job.completed_at},
                       reversed_severity="info" if job.conclusion == "skipped" else "invalid",
                       **ctx)
        if job.status != "completed":
            issues.add("nonterminal_job", "unknown" if run.status == "completed"
                       or job.status is None else "info",
                       field="status", observed=job.status, **ctx)
        else:
            missing = [name for name, value in (
                ("created_at", job.created_at), ("started_at", job.started_at),
                ("completed_at", job.completed_at),
            ) if value is None]
            if missing:
                issues.add("missing_job_timestamps", "info" if job.conclusion == "skipped"
                           else "unknown", fields=missing, **ctx)
            if job.conclusion is None:
                issues.add("missing_job_conclusion", "unknown", field="conclusion", **ctx)
            if not job.steps and job.conclusion != "skipped":
                issues.add("no_step_observations", "unknown", field="steps", **ctx)
        intervals = []
        for step in sorted(job.steps, key=lambda s: s.number):
            step_ctx = {**ctx, "step_number": step.number, "step_status": step.status,
                        "step_conclusion": step.conclusion}
            observed = _times(issues, {"started_at": step.started_at,
                                      "completed_at": step.completed_at},
                              reversed_severity="info" if step.conclusion == "skipped"
                              else "invalid", **step_ctx)
            if step.status != "completed":
                issues.add("nonterminal_step", "unknown" if job.status == "completed"
                           or step.status is None else "info",
                           field="status", observed=step.status, **step_ctx)
            else:
                missing = [name for name, value in (
                    ("started_at", step.started_at), ("completed_at", step.completed_at),
                ) if value is None]
                if missing:
                    issues.add("missing_step_timestamps", "info" if step.conclusion == "skipped"
                               else "unknown", fields=missing, **step_ctx)
                if step.conclusion is None:
                    issues.add("missing_step_conclusion", "unknown", field="conclusion", **step_ctx)
            start, end = observed.get("started_at"), observed.get("completed_at")
            job_start, job_end = times.get("started_at"), times.get("completed_at")
            if job_start is not None and job_end is not None and job_start <= job_end:
                for name, value in observed.items():
                    if value < job_start or value > job_end:
                        issues.add("step_outside_job_window", "info" if step.conclusion == "skipped"
                                   else "invalid", field=name,
                                   observed=value.isoformat(), job_start=job_start.isoformat(),
                                   job_end=job_end.isoformat(), **step_ctx)
            if (start is not None and end is not None and start < end
                    and step.conclusion != "skipped"):
                intervals.append((start, end, step.number))
        previous = None
        for start, end, number in sorted(intervals):
            if previous is not None and start < previous[0]:
                issues.add("overlapping_step_intervals", "invalid", field="started_at",
                           step_number=number, related_step_number=previous[1],
                           observed=start.isoformat(), previous_end=previous[0].isoformat(), **ctx)
            if previous is None or end > previous[0]:
                previous = (end, number)
    if issues.severities["invalid"]:
        state = "inconsistent"
    elif issues.severities["unknown"]:
        state = "incomplete"
    elif run.status != "completed":
        state = "pending"
    elif not current:
        state = "no_observations"
    elif issues.severities["info"]:
        state = "informational_only"
    else:
        state = "consistent_observations"
    total = sum(issues.codes.values())
    return {"run_id": run.id, "attempt": run.run_attempt, "status": run.status,
            "conclusion": run.conclusion, "state": state, "observed_jobs": len(current),
            "observed_steps": sum(len(j.steps) for j in current), "issue_count": total,
            "issue_counts": dict(issues.codes), "severity_counts": dict(issues.severities),
            "issues": issues.rows, "details_truncated": total > len(issues.rows)}


def validate_selection(
    run_ids: list[int] | None, before: int | None, limit: int, max_issues: int,
) -> None:
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    if type(max_issues) is not int or not 0 <= max_issues <= 2000:
        raise ValueError("max-issues must be between 0 and 2000")
    if before is not None and (type(before) is not int or not 1 <= before <= MAX_ID):
        raise ValueError("before must be a positive run ID")
    if run_ids is not None and (
        not run_ids or any(type(n) is not int or not 1 <= n <= MAX_ID for n in run_ids)
        or len(set(run_ids)) > limit or before is not None
    ):
        raise ValueError("explicit IDs must fit --limit and cannot use --before")


def timing_health(
    conn: psycopg.Connection, repo_id: int, *, run_ids: list[int] | None = None,
    before: int | None = None, limit: int = 50, max_issues: int = 200,
) -> dict:
    """Load bounded current-attempt observations in the caller's consistent read-only snapshot."""
    validate_selection(run_ids, before, limit, max_issues)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """SELECT id,repo_id,workflow_id,workflow_path,workflow_name,run_number,run_attempt,
                      event,status,conclusion,head_sha,head_branch,created_at,started_at,updated_at
               FROM run WHERE repo_id=%s
                 AND (%s::bigint[] IS NOT NULL OR status='completed')
                 AND (%s::bigint[] IS NULL OR id=ANY(%s))
                 AND (%s::bigint IS NULL OR id<%s)
               ORDER BY id DESC LIMIT %s""",
            (repo_id, run_ids, run_ids, run_ids, before, before, limit + 1))
        rows = cur.fetchall()
        if run_ids is not None and set(run_ids) != {row["id"] for row in rows}:
            raise ValueError("one or more run IDs are not stored for this repository")
        next_before = rows[limit - 1]["id"] if len(rows) > limit else None
        runs = {row["id"]: Run(**row) for row in rows[:limit]}
        ids = list(runs)
        cur.execute(
            """SELECT j.id,j.run_id,j.repo_id,j.name,j.name_base,j.status,j.conclusion,
                      j.runner_labels,j.runner_group,j.created_at,j.started_at,j.completed_at,
                      j.attempt,j.matrix FROM job j JOIN run r ON r.id=j.run_id
               WHERE j.run_id=ANY(%s) AND j.attempt=r.run_attempt
               ORDER BY j.id LIMIT %s""", (ids, MAX_JOBS + 1))
        rows = cur.fetchall()
        if len(rows) > MAX_JOBS:
            raise ValueError("selected window exceeds 10000 jobs; select fewer runs")
        jobs = {}
        for row in rows:
            if row.pop("repo_id") != repo_id:
                raise ValueError("stored job repository does not match its run")
            job = Job(**row)
            jobs[job.id] = job
            runs[job.run_id].jobs.append(job)
        cur.execute("SELECT job_id,number,name,status,conclusion,started_at,completed_at"
                    " FROM step WHERE job_id=ANY(%s) ORDER BY job_id,number LIMIT %s",
                    (list(jobs), MAX_STEPS + 1))
        rows = cur.fetchall()
        if len(rows) > MAX_STEPS:
            raise ValueError("selected window exceeds 50000 steps; select fewer runs")
        for row in rows:
            jobs[row.pop("job_id")].steps.append(Step(**row))
    reports, remaining = [], max_issues
    for run in runs.values():
        report = inspect_run(run, max_issues=remaining)
        remaining -= len(report["issues"])
        reports.append(report)
    severity: Counter[str] = Counter()
    codes: Counter[str] = Counter()
    for report in reports:
        severity.update(report["severity_counts"])
        codes.update(report["issue_counts"])
    return {"schema_version": 1, "generated_at": datetime.now(UTC).isoformat(),
            "selected_run_ids": list(runs), "next_before": next_before,
            "detail_limit": max_issues,
            "counts": dict(Counter(r["state"] for r in reports)), "runs": reports,
            "severity_counts": dict(severity), "issue_counts": dict(codes),
            "details_truncated": any(r["details_truncated"] for r in reports),
            "scope": "stored current attempts; automatic selection includes completed runs only",
            "meaning": "Consistency of observed rows is not proof of job/step completeness, "
                       "upstream freshness, timing accuracy or causal savings."}
