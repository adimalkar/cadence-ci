"""Explicit before/after observations, stratified by stored execution shape.

Matching metadata is a comparability guard, never evidence of causal savings.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from statistics import median

import psycopg
from psycopg.rows import dict_row

from cadence.models import Job, Run

MAX_RUNS = 500
MAX_JOBS = 50_000
MAX_ID = 2**63 - 1


def validate_selection(before: list[int], after: list[int], min_samples: int) -> None:
    if type(min_samples) is not int or not 2 <= min_samples <= MAX_RUNS:
        raise ValueError("minimum samples must be between 2 and 500 per cohort/profile")
    for ids in (before, after):
        if not ids or any(type(n) is not int or not 1 <= n <= MAX_ID for n in ids):
            raise ValueError("each cohort needs positive stored run IDs")
    if len(set(before) | set(after)) > MAX_RUNS:
        raise ValueError("select at most 500 distinct runs in total")
    if set(before) & set(after):
        raise ValueError("before and after run IDs must be disjoint")


def _aware(value: object) -> bool:
    return isinstance(value, datetime) and value.utcoffset() is not None


@dataclass
class Sample:
    run: Run
    profile: str
    start: datetime
    end: datetime
    execution_seconds: float
    queue_seconds: float

    @property
    def wall_seconds(self) -> float:
        return (self.end - self.start).total_seconds()


def _sample(run: Run, workflow: str) -> tuple[Sample | None, str | None]:
    if run.workflow_path != workflow:
        return None, "different_workflow"
    if run.run_attempt != 1:
        return None, "retried_run"
    if run.status != "completed" or run.conclusion != "success":
        return None, "unsuccessful_or_incomplete_run"
    if (not run.workflow_id or not run.event or not run.head_branch
            or not _aware(run.created_at) or not isinstance(run.head_sha, str)
            or not re.fullmatch(r"[0-9a-f]{40}", run.head_sha)):
        return None, "incomplete_run_metadata"
    if not run.jobs:
        return None, "missing_jobs"
    names: set[str] = set()
    shape = []
    starts, ends = [], []
    execution = queue = 0.0
    for job in run.jobs:
        if job.run_id != run.id or job.attempt != 1:
            return None, "mixed_job_attempts"
        if not job.name or job.name in names:
            return None, "ambiguous_job_names"
        names.add(job.name)
        if job.status != "completed" or job.conclusion != "success":
            return None, "unsuccessful_or_incomplete_job"
        if not job.runner_labels or any(
            not isinstance(label, str) or not label.strip() for label in job.runner_labels
        ):
            return None, "unknown_runner"
        labels = sorted(set(job.runner_labels))
        if any(label.strip().casefold() == "self-hosted" for label in labels):
            return None, "self_hosted_runner"
        if not all(_aware(t) for t in (job.created_at, job.started_at, job.completed_at)):
            return None, "missing_job_timing"
        assert job.created_at is not None and job.started_at is not None
        assert job.completed_at is not None
        if not job.created_at <= job.started_at <= job.completed_at:
            return None, "invalid_job_timing"
        starts.append(job.created_at)
        ends.append(job.completed_at)
        execution += (job.completed_at - job.started_at).total_seconds()
        queue += (job.started_at - job.created_at).total_seconds()
        shape.append({"name": job.name, "matrix": job.matrix, "runner_labels": labels,
                      "runner_group": job.runner_group})
    profile = json.dumps({"repo_id": run.repo_id, "workflow_id": run.workflow_id,
                          "workflow_path": workflow, "event": run.event,
                          "head_branch": run.head_branch,
                          "jobs": sorted(shape, key=lambda j: j["name"])},
                         sort_keys=True, separators=(",", ":"))
    return Sample(run, profile, min(starts), max(ends), execution, queue), None


def _summary(samples: list[Sample]) -> dict:
    return {"run_ids": sorted(s.run.id for s in samples), "sample_count": len(samples),
            "head_shas": sorted({s.run.head_sha for s in samples}),
            "observed_start": min(s.start for s in samples).isoformat(),
            "observed_end": max(s.end for s in samples).isoformat()}


def compare_cohorts(
    before: list[Run], after: list[Run], workflow: str, *, min_samples: int = 5,
) -> dict:
    """Return one comparison per identical profile; never pool different job shapes."""
    validate_selection([r.id for r in before], [r.id for r in after], min_samples)
    if not workflow.strip():
        raise ValueError("workflow path is required")
    groups: dict[str, dict[str, list[Sample]]] = defaultdict(lambda: {"before": [], "after": []})
    excluded = []
    selected = {}
    for side, runs in (("before", before), ("after", after)):
        selected[side] = sorted({r.id for r in runs})
        seen = set()
        for run in runs:
            if run.id in seen:
                continue
            seen.add(run.id)
            sample, reason = _sample(run, workflow)
            if sample is None:
                excluded.append({"run_id": run.id, "cohort": side, "reason": reason})
            else:
                groups[sample.profile][side].append(sample)
    comparisons, declined = [], []
    all_before = [s for g in groups.values() for s in g["before"]]
    all_after = [s for g in groups.values() for s in g["after"]]
    if (all_before and all_after
            and max(s.end for s in all_before) >= min(s.start for s in all_after)):
        raise ValueError("eligible before runs must finish before all eligible after runs start")
    for profile, sides in sorted(groups.items()):
        fingerprint = hashlib.sha256(profile.encode()).hexdigest()
        left, right = sides["before"], sides["after"]
        info = {"profile_id": fingerprint, "profile": json.loads(profile),
                "before_run_ids": sorted(s.run.id for s in left),
                "after_run_ids": sorted(s.run.id for s in right)}
        if len(left) < min_samples or len(right) < min_samples:
            reason = "unmatched_profile" if not left or not right else "insufficient_samples"
            declined.append({**info, "reason": reason})
            for side, samples in sides.items():
                excluded.extend({"run_id": s.run.id, "cohort": side, "reason": reason}
                                for s in samples)
            continue
        metrics = {}
        for name in ("wall_seconds", "execution_seconds", "queue_seconds"):
            b = float(median(getattr(s, name) for s in left))
            a = float(median(getattr(s, name) for s in right))
            metrics[name] = {"before_median": b, "after_median": a, "change": a - b,
                             "reduction_pct": (b - a) / b * 100 if b > 0 else None}
        comparisons.append({**info, "before": _summary(left), "after": _summary(right),
                            "metrics": metrics})
    return {"schema_version": 1, "interpretation": "observed_timing_change",
            "workflow": workflow, "minimum_samples_per_profile": min_samples,
            "selected": selected, "comparisons": comparisons, "declined_profiles": declined,
            "excluded_runs": sorted(excluded, key=lambda row: (row["cohort"], row["run_id"])),
            "limitations": [
                "Matching stored metadata does not establish causality or realized savings.",
                "Successful first attempts only; incomplete or changed job shapes are excluded.",
                "Runner labels do not pin hardware/images; workload, config and time may differ.",
                "Missing jobs can be undetectable without authoritative ingest completeness.",
            ]}


def compare_stored_runs(
    conn: psycopg.Connection, repo_id: int, workflow: str,
    before_ids: list[int], after_ids: list[int], *, min_samples: int = 5,
) -> dict:
    """Load explicit IDs in the caller's read-only consistent transaction; no API calls."""
    validate_selection(before_ids, after_ids, min_samples)
    ids = sorted(set(before_ids) | set(after_ids))
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """SELECT id, repo_id, workflow_id, workflow_path, workflow_name, run_number,
                      run_attempt, event, status, conclusion, head_sha, head_branch,
                      created_at, started_at, updated_at, pull_request_number, tree_sha
               FROM run WHERE repo_id = %s AND id = ANY(%s)""", (repo_id, ids))
        runs = {row["id"]: Run(**row) for row in cur.fetchall()}
        missing = sorted(set(ids) - runs.keys())
        if missing:
            raise ValueError(f"run IDs are not stored for this repository: {missing}")
        cur.execute(
            """SELECT id, run_id, repo_id, name, name_base, status, conclusion, runner_labels,
                      runner_group, created_at, started_at, completed_at, attempt, matrix
               FROM job WHERE run_id = ANY(%s) ORDER BY id LIMIT %s""", (ids, MAX_JOBS + 1))
        jobs = cur.fetchall()
        if len(jobs) > MAX_JOBS:
            raise ValueError("selected runs exceed the 50000-job bound; select fewer runs")
        for row in jobs:
            if row.pop("repo_id") != repo_id:
                raise ValueError("stored job belongs to a different repository")
            runs[row["run_id"]].jobs.append(Job(**row))
    return compare_cohorts([runs[n] for n in sorted(set(before_ids))],
                           [runs[n] for n in sorted(set(after_ids))], workflow,
                           min_samples=min_samples)
