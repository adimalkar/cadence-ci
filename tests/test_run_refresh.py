"""Exact-run reads and resumable orchestration, without database/network fixtures."""
from dataclasses import replace
from datetime import UTC, datetime

import httpx
import pytest
import respx

from cadence.models import Repo, Run
from cadence.providers.base import AccessDenied, Expired, NotFound, RateLimited
from cadence.providers.github import API_ROOT, GitHubProvider
from cadence.run_refresh import refresh_metadata

REPO = Repo(42, "acme", "widget", False, "main")
T0 = datetime(2026, 8, 1, tzinfo=UTC)


def snapshot(run_id=100, **overrides):
    run = Run(run_id, REPO.id, 7, ".github/workflows/ci.yml", "CI", 1, 1,
              "push", "completed", "success", "a" * 40, "main", T0, T0, T0)
    return replace(run, **overrides)


def payload():
    return {"id": 100, "repository": {"id": REPO.id}, "workflow_id": 7,
            "path": ".github/workflows/ci.yml", "name": "CI", "run_number": 1,
            "run_attempt": 1, "event": "push", "status": "completed", "conclusion": "success",
            "head_sha": "a" * 40, "head_branch": "main", "created_at": T0.isoformat(),
            "updated_at": T0.isoformat(), "pull_requests": [{"number": 9}]}


@respx.mock
async def test_exact_read_is_one_get_without_polling_etag():
    route = respx.get(f"{API_ROOT}/repos/acme/widget/actions/runs/100").mock(
        return_value=httpx.Response(200, json=payload()))
    provider = GitHubProvider("test-token")
    try:
        run = await provider.fetch_run(REPO, 100)
    finally:
        await provider.aclose()
    assert run.id == 100 and run.pull_request_number == 9
    assert route.call_count == 1
    assert len(respx.calls) == 1
    assert "if-none-match" not in route.calls[0].request.headers
    assert not route.calls[0].request.url.query


@pytest.mark.parametrize("bad", [[], {"id": 101}, {"id": 100, "repository": {"id": 99}},
                                {"id": 100, "repository": []}])
@respx.mock
async def test_exact_read_rejects_unrelated_or_malformed_payload(bad):
    respx.get(f"{API_ROOT}/repos/acme/widget/actions/runs/100").mock(
        return_value=httpx.Response(200, json=bad))
    provider = GitHubProvider("test-token")
    try:
        with pytest.raises(ValueError):
            await provider.fetch_run(REPO, 100)
    finally:
        await provider.aclose()


class Provider:
    def __init__(self, values):
        self.values = iter(values)
        self.calls = []

    async def fetch_run(self, repo, run_id):
        self.calls.append(run_id)
        value = next(self.values)
        if isinstance(value, Exception):
            raise value
        return value


@pytest.mark.parametrize("failure,reason", [
    (RateLimited(23), "rate_limited"), (AccessDenied("denied"), "access_denied"),
    (httpx.ConnectError("offline"), "transport_error"),
])
async def test_partial_stop_keeps_committed_outcome_and_pending_ids(failure, reason):
    provider = Provider([snapshot(3), failure])
    saved = []
    report = await refresh_metadata(provider, REPO, [3, 3, 2, 1],
                                    lambda run: saved.append(run.id) or "refreshed")
    result = report.as_dict()
    assert saved == [3]
    assert provider.calls == [3, 2]
    assert result["counts"] == {"refreshed": 1}
    assert result["remaining_run_ids"] == [2, 1]
    assert result["stopped"]["reason"] == reason
    if reason == "rate_limited":
        assert result["stopped"]["retry_after_seconds"] == 23


async def test_unavailable_and_invalid_snapshots_continue_without_mutation():
    provider = Provider([NotFound(), Expired(), snapshot(99),
                         snapshot(4, updated_at=None), snapshot(5)])
    saved = []
    report = await refresh_metadata(provider, REPO, [1, 2, 3, 4, 5],
                                    lambda run: saved.append(run.id) or "refreshed")
    assert saved == [5]
    assert report.as_dict()["counts"] == {"unavailable": 2, "invalid_metadata": 2, "refreshed": 1}
    assert not report.as_dict()["remaining_run_ids"]
    assert report.stopped is None


async def test_persistence_failure_keeps_current_id_pending():
    provider = Provider([snapshot(2), snapshot(1)])

    def persist(run):
        if run.id == 1:
            raise ValueError("inconsistent identity")
        return "refreshed"

    result = (await refresh_metadata(provider, REPO, [2, 1], persist)).as_dict()
    assert result["counts"] == {"refreshed": 1}
    assert result["remaining_run_ids"] == [1]
    assert result["stopped"]["reason"] == "persistence_error"


@pytest.mark.parametrize("ids", [[True], [0], [-1], [2**63], list(range(1, 502))])
async def test_invalid_selection_never_calls_provider(ids):
    provider = Provider([])
    with pytest.raises(ValueError):
        await refresh_metadata(provider, REPO, ids, lambda run: "refreshed")
    assert not provider.calls
