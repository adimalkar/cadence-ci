"""Repair observations and bounded provider reads, without database state."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from cadence.job_refresh import AttemptTarget, repair_jobs, validate_jobs, validate_selection
from cadence.models import Job, Repo, Step
from cadence.providers.base import AccessDenied, NotFound, RateLimited
from cadence.providers.github import API_ROOT, GitHubProvider

REPO = Repo(7_880_001, "repair-test", "widget", False, "main")
RUN_ID = 7_880_100
T0 = datetime(2026, 11, 18, tzinfo=UTC)


def target(run_id=RUN_ID, **overrides):
    return replace(
        AttemptTarget(run_id, REPO.id, 1, 1, "a" * 40, T0 + timedelta(seconds=200)), **overrides
    )


def job(job_id=7_880_900, **overrides):
    step = Step(
        1, "build", "completed", "success", T0 + timedelta(seconds=10), T0 + timedelta(seconds=110)
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
            T0 + timedelta(seconds=110),
            1,
            steps=[step],
        ),
        **overrides,
    )


class Provider:
    def __init__(self, values):
        self.values = iter(values)
        self.calls = []

    async def fetch_jobs(self, repo, run_id, *, attempt, max_pages):
        self.calls.append((run_id, attempt, max_pages))
        result = next(self.values)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.mark.parametrize(
    "failure,reason",
    [
        (RateLimited(17), "rate_limited"),
        (AccessDenied(), "access_denied"),
        (httpx.ConnectError("offline"), "transport_error"),
    ],
)
async def test_partial_stop_preserves_outcome_and_exact_pending_attempts(failure, reason):
    provider = Provider([[job()], failure])
    saved = []
    result = await repair_jobs(
        provider,
        REPO,
        [target(), target(RUN_ID + 1)],
        lambda t, jobs: saved.append(t.run_id) or {"status": "refreshed"},
    )
    assert saved == [RUN_ID]
    assert result["counts"] == {"refreshed": 1}
    assert result["remaining_attempts"] == [{"run_id": RUN_ID + 1, "attempt": 1}]
    assert result["stopped"]["reason"] == reason
    assert provider.calls == [(RUN_ID, 1, 10), (RUN_ID + 1, 1, 10)]
    if reason == "rate_limited":
        assert result["stopped"]["retry_after_seconds"] == 17


async def test_unavailable_invalid_and_empty_observations_are_explicit():
    provider = Provider([NotFound(), [job(attempt=2)], []])
    result = await repair_jobs(
        provider, REPO, [target(RUN_ID + i) for i in range(3)], lambda t, jobs: {"status": "empty"}
    )
    assert [r["status"] for r in result["outcomes"]] == ["unavailable", "invalid_metadata", "empty"]
    assert not result["remaining_attempts"] and result["stopped"] is None
    assert result["coverage"].startswith("unverified")


async def test_persistence_error_keeps_current_attempt_pending():
    def persist(*args):
        raise ValueError("identity conflict")

    result = await repair_jobs(Provider([[job()]]), REPO, [target()], persist)
    assert result["stopped"]["reason"] == "persistence_error"
    assert result["remaining_attempts"] == [{"run_id": RUN_ID, "attempt": 1}]


@pytest.mark.parametrize(
    "overrides",
    [
        {"id": True},
        {"run_id": 999},
        {"attempt": 2},
        {"status": "in_progress"},
        {"conclusion": None},
        {"created_at": T0.replace(tzinfo=None)},
        {"started_at": T0 - timedelta(seconds=1)},
        {"steps": [Step(1, "build", "in_progress", None, T0, None)]},
        {"steps": [Step(0, "build", "completed", "success", T0, T0)]},
    ],
)
def test_invalid_observation_never_reaches_persistence(overrides):
    with pytest.raises(ValueError):
        validate_jobs(target(), [job(**overrides)], 10)


def test_duplicates_are_rejected_and_missing_timings_are_allowed_as_unknown():
    with pytest.raises(ValueError):
        validate_jobs(target(), [job(), job()], 10)
    duplicate = job()
    duplicate.steps *= 2
    with pytest.raises(ValueError):
        validate_jobs(target(), [duplicate], 10)
    validate_jobs(target(), [job(created_at=None, started_at=None, completed_at=None)], 10)


@pytest.mark.parametrize(
    "ids,attempt,pages",
    [
        ([], None, 10),
        ([0], None, 10),
        ([True], None, 10),
        ([2**63], None, 10),
        (list(range(1, 102)), None, 10),
        ([1], 0, 10),
        ([1], None, 0),
        ([1], None, 101),
    ],
)
def test_selection_bounds(ids, attempt, pages):
    with pytest.raises(ValueError):
        validate_selection(ids, attempt, pages)


def raw(job_id=7_880_900, **overrides):
    return {
        "id": job_id,
        "run_id": RUN_ID,
        "run_attempt": 1,
        "name": "build",
        "status": "completed",
        "conclusion": "success",
        **overrides,
    }


URL = f"{API_ROOT}/repos/{REPO.owner}/{REPO.name}/actions/runs/{RUN_ID}/attempts/1/jobs"


@respx.mock
async def test_bounded_read_fetches_exact_attempt_and_no_etag():
    route = respx.get(URL).mock(
        side_effect=[
            httpx.Response(200, json={"total_count": 2, "jobs": [raw()]}),
            httpx.Response(200, json={"total_count": 2, "jobs": [raw(7_880_901)]}),
        ]
    )
    provider = GitHubProvider("test-token")
    try:
        jobs = await provider.fetch_jobs(REPO, RUN_ID, attempt=1, max_pages=2)
    finally:
        await provider.aclose()
    assert len(jobs) == 2 and route.call_count == 2
    assert all("if-none-match" not in c.request.headers for c in route.calls)
    assert all("filter" not in c.request.url.params for c in route.calls)


@pytest.mark.parametrize(
    "payloads,pages",
    [
        ([{}], 1),
        ([{"total_count": 101, "jobs": [raw()]}], 1),
        ([{"total_count": 2, "jobs": []}], 1),
        ([{"total_count": 2, "jobs": [raw(run_id=999)]}], 1),
        ([{"total_count": 2, "jobs": [raw(run_attempt=2)]}], 1),
        ([{"total_count": 2, "jobs": [raw()]}], 1),
        ([{"total_count": 2, "jobs": [raw()]}, {"total_count": 1, "jobs": []}], 2),
    ],
)
@respx.mock
async def test_page_uncertainty_returns_no_partial_list(payloads, pages):
    respx.get(URL).mock(side_effect=[httpx.Response(200, json=p) for p in payloads])
    provider = GitHubProvider("test-token")
    try:
        with pytest.raises(ValueError):
            await provider.fetch_jobs(REPO, RUN_ID, attempt=1, max_pages=pages)
    finally:
        await provider.aclose()
