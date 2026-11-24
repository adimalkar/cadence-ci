"""Pin the meaning of observed changes and the reasons to decline comparisons."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from cadence.cohorts import compare_cohorts, validate_selection
from cadence.models import Job, Run

T0 = datetime(2026, 8, 1, tzinfo=UTC)
WORKFLOW = ".github/workflows/ci.yml"
REPO_ID = 8_650_001


def run(run_id, *, day=0, seconds=100, **overrides):
    start = T0 + timedelta(days=day)
    jobs = [
        Job(
            run_id * 10 + i,
            run_id,
            name,
            name,
            "completed",
            "success",
            ["ubuntu-latest"],
            None,
            start,
            start + timedelta(seconds=10),
            start + timedelta(seconds=10 + seconds),
            1,
        )
        for i, name in enumerate(("build", "test"))
    ]
    result = Run(
        run_id,
        REPO_ID,
        7,
        WORKFLOW,
        "CI",
        1,
        1,
        "push",
        "completed",
        "success",
        "a" * 40,
        "main",
        start,
        start,
        start,
        jobs=jobs,
    )
    return replace(result, **overrides)


def cohorts():
    return [run(1), run(2, seconds=200)], [run(3, day=2, seconds=50), run(4, day=3, seconds=100)]


def compare(before=None, after=None):
    default_before, default_after = cohorts()
    return compare_cohorts(
        before if before is not None else default_before,
        after if after is not None else default_after,
        WORKFLOW,
        min_samples=2,
    )


def test_parallel_wall_execution_and_queue_are_separate_observations():
    result = compare()
    assert result["interpretation"] == "observed_timing_change"
    (comparison,) = result["comparisons"]
    assert comparison["before"]["run_ids"] == [1, 2]
    assert comparison["after"]["sample_count"] == 2
    metrics = comparison["metrics"]
    assert metrics["wall_seconds"] == {
        "before_median": 160,
        "after_median": 85,
        "change": -75,
        "reduction_pct": 46.875,
    }
    assert metrics["execution_seconds"]["before_median"] == 300
    assert metrics["execution_seconds"]["after_median"] == 150
    assert metrics["queue_seconds"]["before_median"] == 20
    assert metrics["queue_seconds"]["change"] == 0
    assert not result["excluded_runs"]
    assert "realized savings" in result["limitations"][0]


def test_zero_baseline_has_no_percentage_and_increase_has_negative_reduction():
    before, after = cohorts()
    for sample in before:
        for job in sample.jobs:
            job.started_at = job.created_at
            job.completed_at = job.created_at
    metrics = compare(before, after)["comparisons"][0]["metrics"]
    assert metrics["execution_seconds"]["reduction_pct"] is None
    before, after = cohorts()
    for sample in after:
        for job in sample.jobs:
            job.completed_at += timedelta(seconds=1000)
    assert compare(before, after)["comparisons"][0]["metrics"]["wall_seconds"]["reduction_pct"] < 0


def test_duplicate_selection_and_order_do_not_change_evidence():
    before, after = cohorts()
    expected = compare(before, after)
    assert compare([before[1], before[0], before[0]], list(reversed(after))) == expected


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("workflow_path", "other.yml", "different_workflow"),
        ("run_attempt", 2, "retried_run"),
        ("status", "in_progress", "unsuccessful_or_incomplete_run"),
        ("conclusion", "failure", "unsuccessful_or_incomplete_run"),
        ("workflow_id", None, "incomplete_run_metadata"),
        ("head_branch", None, "incomplete_run_metadata"),
        ("event", None, "incomplete_run_metadata"),
        ("head_sha", "short", "incomplete_run_metadata"),
        ("created_at", T0.replace(tzinfo=None), "incomplete_run_metadata"),
        ("jobs", [], "missing_jobs"),
    ],
)
def test_ineligible_run_has_explicit_reason(field, value, reason):
    before, after = cohorts()
    before[0] = replace(before[0], **{field: value})
    result = compare(before, after)
    assert {"run_id": 1, "cohort": "before", "reason": reason} in result["excluded_runs"]
    assert not result["comparisons"]


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("attempt", 2, "mixed_job_attempts"),
        ("run_id", 99, "mixed_job_attempts"),
        ("name", "test", "ambiguous_job_names"),
        ("status", "in_progress", "unsuccessful_or_incomplete_job"),
        ("conclusion", "skipped", "unsuccessful_or_incomplete_job"),
        ("runner_labels", [], "unknown_runner"),
        ("runner_labels", [None], "unknown_runner"),
        ("runner_labels", ["Self-Hosted"], "self_hosted_runner"),
        ("created_at", None, "missing_job_timing"),
        ("started_at", T0.replace(tzinfo=None), "missing_job_timing"),
        ("started_at", T0 - timedelta(seconds=1), "invalid_job_timing"),
    ],
)
def test_ineligible_job_excludes_the_run(field, value, reason):
    before, after = cohorts()
    setattr(before[0].jobs[0], field, value)
    result = compare(before, after)
    assert {"run_id": 1, "cohort": "before", "reason": reason} in result["excluded_runs"]
    assert not result["comparisons"]


@pytest.mark.parametrize(
    "change",
    ["event", "branch", "workflow_id", "name", "labels", "matrix", "runner_group", "extra_job"],
)
def test_changed_execution_profile_is_never_pooled(change):
    before, after = cohorts()
    for sample in after:
        match change:
            case "event":
                sample.event = "pull_request"
            case "branch":
                sample.head_branch = "release"
            case "workflow_id":
                sample.workflow_id = 8
            case "name":
                sample.jobs[0].name = "new-build"
            case "labels":
                sample.jobs[0].runner_labels = ["windows-latest"]
            case "matrix":
                sample.jobs[0].matrix = {"os": "ubuntu"}
            case "runner_group":
                sample.jobs[0].runner_group = "other"
            case "extra_job":
                sample.jobs.append(replace(sample.jobs[0], name="lint"))
    result = compare(before, after)
    assert not result["comparisons"]
    assert len(result["declined_profiles"]) == 2
    assert all(r["reason"] == "unmatched_profile" for r in result["excluded_runs"])


def test_distinct_profiles_are_reported_separately_with_sufficient_samples():
    before, after = cohorts()
    before += [run(5, event="schedule"), run(6, event="schedule")]
    after += [run(7, day=2, event="schedule"), run(8, day=3, event="schedule")]
    result = compare(before, after)
    assert len(result["comparisons"]) == 2
    assert {c["profile"]["event"] for c in result["comparisons"]} == {"push", "schedule"}


def test_reordered_jobs_labels_and_matrix_keys_match():
    before, after = cohorts()
    for sample in before + after:
        sample.jobs[0].runner_labels = ["Linux", "X64"]
        sample.jobs[0].matrix = {"os": "linux", "python": "3.12"}
    for sample in after:
        sample.jobs.reverse()
        sample.jobs[1].runner_labels = ["X64", "Linux", "Linux"]
        sample.jobs[1].matrix = {"python": "3.12", "os": "linux"}
    assert len(compare(before, after)["comparisons"]) == 1


def test_default_sample_floor_declines_two_per_side():
    before, after = cohorts()
    result = compare_cohorts(before, after, WORKFLOW)
    assert not result["comparisons"]
    assert result["declined_profiles"][0]["reason"] == "insufficient_samples"


def test_overlapping_windows_are_rejected():
    before, after = cohorts()
    after[0] = run(3)
    with pytest.raises(ValueError, match="finish before"):
        compare(before, after)


@pytest.mark.parametrize(
    "before,after,minimum",
    [
        ([], [2], 2),
        ([1], [], 2),
        ([1], [1], 2),
        ([True], [2], 2),
        ([0], [2], 2),
        ([1], [2**63], 2),
        ([1], [2], 1),
        ([1], [2], 501),
        (list(range(1, 501)), [501], 2),
    ],
)
def test_invalid_selection_is_rejected(before, after, minimum):
    with pytest.raises(ValueError):
        validate_selection(before, after, minimum)
