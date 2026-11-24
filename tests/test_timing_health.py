"""Timestamp observations are inspected without clamping or completeness claims."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from cadence.models import Job, Run, Step
from cadence.timing_health import inspect_run, validate_selection

REPO_ID = 9_240_001
RUN_ID = 9_240_100
T0 = datetime(2026, 11, 18, tzinfo=UTC)


def job(job_id=9_240_900, **overrides):
    step = Step(
        1, "build", "completed", "success", T0 + timedelta(seconds=20), T0 + timedelta(seconds=90)
    )
    return replace(
        Job(
            job_id,
            RUN_ID,
            "build",
            "build",
            "completed",
            "success",
            ["ubuntu-latest"],
            None,
            T0,
            T0 + timedelta(seconds=10),
            T0 + timedelta(seconds=100),
            1,
            steps=[step],
        ),
        **overrides,
    )


def run(**overrides):
    return replace(
        Run(
            RUN_ID,
            REPO_ID,
            7,
            "ci.yml",
            "CI",
            1,
            1,
            "push",
            "completed",
            "success",
            "a" * 40,
            "main",
            T0,
            T0,
            T0,
            jobs=[job()],
        ),
        **overrides,
    )


def test_consistent_observations_are_not_claimed_as_complete():
    report = inspect_run(run())
    assert report["state"] == "consistent_observations"
    assert report["observed_jobs"] == report["observed_steps"] == 1
    assert report["issue_count"] == 0 and not report["details_truncated"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("created_at", T0 + timedelta(seconds=30)),
        ("started_at", T0 + timedelta(seconds=110)),
        ("completed_at", T0 - timedelta(seconds=1)),
    ],
)
def test_reversed_job_times_are_not_clamped(field, value):
    report = inspect_run(run(jobs=[job(**{field: value})]))
    assert report["state"] == "inconsistent"
    assert report["issue_counts"]["reversed_timestamps"] >= 1
    assert any(issue["observed"] for issue in report["issues"])


@pytest.mark.parametrize("value", [T0.replace(tzinfo=None), "not a datetime"])
def test_invalid_timestamp_has_explicit_field_and_job_id(value):
    report = inspect_run(run(jobs=[job(created_at=value)]))
    issue = next(i for i in report["issues"] if i["code"] == "invalid_timestamp")
    assert issue["field"] == "created_at" and issue["job_id"] == job().id
    assert report["state"] == "inconsistent"


def test_missing_timestamps_and_steps_are_unknown_not_zero_duration():
    item = job(created_at=None, started_at=None, completed_at=None, steps=[])
    report = inspect_run(run(jobs=[item]))
    assert report["state"] == "incomplete"
    assert report["issue_counts"] == {"missing_job_timestamps": 1, "no_step_observations": 1}
    assert report["issues"][0]["fields"] == ["created_at", "started_at", "completed_at"]


def test_skipped_missing_times_are_info_and_skipped_empty_run_is_no_observations():
    report = inspect_run(
        run(
            jobs=[
                job(
                    conclusion="skipped",
                    created_at=None,
                    started_at=None,
                    completed_at=None,
                    steps=[],
                )
            ]
        )
    )
    assert report["state"] == "informational_only"
    assert report["severity_counts"] == {"info": 1}
    empty = inspect_run(run(conclusion="skipped", jobs=[]))
    assert empty["state"] == "no_observations" and empty["severity_counts"] == {"info": 1}


def test_active_job_is_pending_until_run_says_completed():
    item = job(status="in_progress", conclusion=None, completed_at=None, steps=[])
    report = inspect_run(run(status="in_progress", conclusion=None, jobs=[item]))
    assert report["state"] == "pending" and report["severity_counts"] == {"info": 1}
    assert inspect_run(run(jobs=[item]))["state"] == "incomplete"


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("started_at", T0 + timedelta(seconds=95), "reversed_timestamps"),
        ("started_at", T0 + timedelta(seconds=5), "step_outside_job_window"),
        ("completed_at", T0 + timedelta(seconds=101), "step_outside_job_window"),
        ("started_at", None, "missing_step_timestamps"),
        ("conclusion", None, "missing_step_conclusion"),
        ("status", "in_progress", "nonterminal_step"),
    ],
)
def test_step_observations_include_step_number(field, value, code):
    item = job()
    setattr(item.steps[0], field, value)
    report = inspect_run(run(jobs=[item]))
    issue = next(i for i in report["issues"] if i["code"] == code)
    assert issue["step_number"] == 1 and issue["job_id"] == item.id


def test_overlapping_intervals_are_detected_but_gaps_and_zero_lengths_are_allowed():
    item = job()
    item.steps[0].completed_at = T0 + timedelta(seconds=30)
    second = replace(
        item.steps[0],
        number=2,
        started_at=T0 + timedelta(seconds=25),
        completed_at=T0 + timedelta(seconds=50),
    )
    item.steps.append(second)
    report = inspect_run(run(jobs=[item]))
    assert report["issue_counts"] == {"overlapping_step_intervals": 1}
    assert report["issues"][0]["related_step_number"] == 1
    second.started_at = T0 + timedelta(seconds=40)
    item.steps.append(
        replace(second, number=3, started_at=second.started_at, completed_at=second.started_at)
    )
    assert inspect_run(run(jobs=[item]))["issue_count"] == 0


def test_old_attempts_do_not_pollute_current_attempt_inspection():
    report = inspect_run(run(jobs=[job(attempt=2)]))
    assert report["observed_jobs"] == 0 and report["state"] == "incomplete"
    assert report["issue_counts"] == {"no_current_attempt_jobs": 1}


def test_capped_details_do_not_change_totals_or_state():
    item = job(created_at=None, conclusion=None, steps=[])
    full = inspect_run(run(jobs=[item]))
    for budget in (0, 1):
        capped = inspect_run(run(jobs=[item]), max_issues=budget)
        assert capped["issue_counts"] == full["issue_counts"]
        assert capped["state"] == full["state"] == "incomplete"
        assert len(capped["issues"]) == budget and capped["details_truncated"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"limit": 0},
        {"limit": 501},
        {"max_issues": -1},
        {"max_issues": 2001},
        {"run_ids": []},
        {"run_ids": [True]},
        {"run_ids": [2**63]},
        {"before": 0},
        {"run_ids": [1], "before": 2},
        {"run_ids": [1, 2], "limit": 1},
    ],
)
def test_bad_selection_fails_before_database_access(kwargs):
    with pytest.raises(ValueError):
        validate_selection(
            **{"run_ids": None, "before": None, "limit": 50, "max_issues": 200, **kwargs}
        )


def test_unknown_status_is_not_assumed_to_be_pending_work():
    report = inspect_run(run(status=None))
    assert report["state"] == "incomplete"
    assert report["issue_counts"] == {"unknown_run_status": 1}
    assert inspect_run(run(status="in_progress", jobs=[job(status=None)]))["state"] == "incomplete"


def test_skipped_reversed_times_remain_visible_as_unmeasured_information():
    report = inspect_run(
        run(jobs=[job(conclusion="skipped", steps=[], started_at=T0 + timedelta(seconds=110))])
    )
    assert report["state"] == "informational_only"
    assert report["severity_counts"] == {"info": 1}
    assert report["issue_counts"] == {"reversed_timestamps": 1}
    assert report["issues"][0]["job_conclusion"] == "skipped"


def test_skipped_step_intervals_are_not_counted_as_executed_overlaps():
    item = job()
    item.steps.append(replace(item.steps[0], number=2, conclusion="skipped",
                              started_at=T0 + timedelta(seconds=5)))
    report = inspect_run(run(jobs=[item]))
    assert report["issue_counts"] == {"step_outside_job_window": 1}
    assert report["state"] == "informational_only"
    assert report["issues"][0]["step_conclusion"] == "skipped"
