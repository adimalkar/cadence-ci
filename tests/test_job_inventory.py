"""Validated attempt inventories; pagination or identity uncertainty cannot certify coverage."""

from dataclasses import replace
from datetime import UTC, datetime

import httpx
import pytest
import respx

from cadence.job_inventory import JobInventory
from cadence.models import Job, Repo
from cadence.providers.base import RateLimited
from cadence.providers.github import API_ROOT, GitHubProvider

REPO = Repo(9_760_001, "inventory-test", "widget", False, "main")
T0 = datetime(2026, 11, 18, tzinfo=UTC)
RUN_ID = 9_760_100


def job(job_id=9_760_900, attempt=1, **overrides):
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
            T0,
            T0,
            attempt,
        ),
        **overrides,
    )


def inventory(jobs=None, **overrides):
    jobs = (job(),) if jobs is None else tuple(jobs)
    return replace(JobInventory(REPO.id, RUN_ID, 1, len(jobs), 1, T0, jobs), **overrides)


def raw(job_id=9_760_900, **overrides):
    return {
        "id": job_id,
        "run_id": RUN_ID,
        "run_attempt": 1,
        "name": "build",
        "status": "completed",
        "conclusion": "success",
        "labels": ["ubuntu-latest"],
        "created_at": T0.isoformat(),
        "started_at": T0.isoformat(),
        "completed_at": T0.isoformat(),
        **overrides,
    }


def endpoint(attempt=1):
    return (
        f"{API_ROOT}/repos/{REPO.owner}/{REPO.name}/actions/runs/{RUN_ID}/attempts/{attempt}/jobs"
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"repo_id": True},
        {"run_id": 0},
        {"attempt": 0},
        {"expected_count": 2},
        {"expected_count": 10_001},
        {"pages": 0},
        {"observed_at": T0.replace(tzinfo=None)},
        {"jobs": (job(run_id=999),)},
        {"jobs": (job(attempt=2),)},
        {"jobs": (job(), job()), "expected_count": 2},
    ],
)
def test_receipt_validation_rejects_ambiguous_inventory(overrides):
    with pytest.raises(ValueError):
        inventory(**overrides).validate()


@respx.mock
async def test_explicit_attempt_pagination_has_no_etag_or_latest_filter():
    route = respx.get(endpoint()).mock(
        side_effect=[
            httpx.Response(200, json={"total_count": 2, "jobs": [raw()]}),
            httpx.Response(200, json={"total_count": 2, "jobs": [raw(9_760_901)]}),
        ]
    )
    provider = GitHubProvider("test-token")
    try:
        result = await provider.fetch_job_inventory(REPO, RUN_ID, attempt=1)
    finally:
        await provider.aclose()
    result.validate()
    assert result.expected_count == 2 and result.pages == 2
    assert [j.id for j in result.jobs] == [9_760_900, 9_760_901]
    assert result.observed_at.utcoffset() is not None
    assert [c.request.url.params["page"] for c in route.calls] == ["1", "2"]
    assert all("filter" not in c.request.url.params for c in route.calls)
    assert all("if-none-match" not in c.request.headers for c in route.calls)


@respx.mock
async def test_explicit_zero_is_known_empty_and_in_progress_job_is_not_hidden():
    route = respx.get(endpoint()).mock(
        return_value=httpx.Response(200, json={"total_count": 0, "jobs": []})
    )
    provider = GitHubProvider("test-token")
    try:
        result = await provider.fetch_job_inventory(REPO, RUN_ID, attempt=1)
        assert result.jobs == () and result.expected_count == 0
        route.mock(
            return_value=httpx.Response(
                200, json={"total_count": 1, "jobs": [raw(status="in_progress")]}
            )
        )
        pending = await provider.fetch_job_inventory(REPO, RUN_ID, attempt=1)
        assert pending.jobs[0].status == "in_progress"
    finally:
        await provider.aclose()


@pytest.mark.parametrize(
    "payloads",
    [
        [[]],
        [{}],
        [{"jobs": [], "total_count": None}],
        [{"jobs": [], "total_count": True}],
        [{"jobs": [], "total_count": 10_001}],
        [{"jobs": [], "total_count": 1}],
        [{"jobs": [raw()], "total_count": 0}],
        [{"jobs": [raw(run_id=999)], "total_count": 1}],
        [{"jobs": [raw(run_attempt=2)], "total_count": 1}],
        [{"jobs": [raw(id=True)], "total_count": 1}],
        [{"jobs": [raw()], "total_count": 2}, {"jobs": [raw()], "total_count": 2}],
        [{"jobs": [raw()], "total_count": 2}, {"jobs": [], "total_count": 1}],
        [{"jobs": [raw()], "total_count": 2}, {"jobs": [], "total_count": 2}],
    ],
)
@respx.mock
async def test_bad_or_partial_pages_never_return_receipt(payloads):
    respx.get(endpoint()).mock(side_effect=[httpx.Response(200, json=p) for p in payloads])
    provider = GitHubProvider("test-token")
    try:
        with pytest.raises(ValueError):
            await provider.fetch_job_inventory(REPO, RUN_ID, attempt=1)
    finally:
        await provider.aclose()


@respx.mock
async def test_next_page_contradicting_total_is_rejected():
    respx.get(endpoint()).mock(
        return_value=httpx.Response(
            200,
            json={"total_count": 1, "jobs": [raw()]},
            headers={"Link": f'<{endpoint()}?page=2>; rel="next"'},
        )
    )
    provider = GitHubProvider("test-token")
    try:
        with pytest.raises(ValueError, match="next-page"):
            await provider.fetch_job_inventory(REPO, RUN_ID, attempt=1)
    finally:
        await provider.aclose()


@respx.mock
async def test_later_rate_limit_preserves_delay_and_returns_no_partial_receipt():
    respx.get(endpoint()).mock(
        side_effect=[
            httpx.Response(200, json={"total_count": 2, "jobs": [raw()]}),
            httpx.Response(429, headers={"Retry-After": "17"}),
        ]
    )
    provider = GitHubProvider("test-token")
    try:
        with pytest.raises(RateLimited) as exc:
            await provider.fetch_job_inventory(REPO, RUN_ID, attempt=1)
        assert exc.value.retry_after_seconds == 17
    finally:
        await provider.aclose()
