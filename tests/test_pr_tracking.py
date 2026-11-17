"""Fix PR links are explicit, repository-scoped and never a savings attribution."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
import respx

from cadence.models import Repo
from cadence.pr_tracking import PullRequestSnapshot
from cadence.providers.github import API_ROOT, GitHubProvider

NOW = datetime(2026, 11, 17, tzinfo=UTC)
HEAD = "a" * 40
MERGE = "b" * 40
REPO = Repo(id=1, owner="acme", name="widget", is_private=False, default_branch="main")


def snapshot(**over):
    return replace(PullRequestSnapshot(
        1, 42, 1234, "https://github.com/acme/widget/pull/42", HEAD, "main", "open", NOW,
    ), **over)


def payload():
    return {
        "id": 1234, "number": 42, "html_url": "https://github.com/acme/widget/pull/42",
        "state": "open", "merged": False, "merged_at": None,
        "updated_at": NOW.isoformat(), "head": {"sha": HEAD},
        "base": {"ref": "main", "repo": {"id": 1}},
        "merge_commit_sha": MERGE,  # an unmerged PR's ephemeral test merge is not evidence
    }


def test_open_test_merge_sha_never_becomes_merge_evidence():
    assert PullRequestSnapshot.from_github(payload()) == snapshot()


@pytest.mark.parametrize("over", [
    {"state": "invalid"}, {"head_sha": "main"}, {"base_ref": ""},
    {"repo_id": 0}, {"number": True}, {"provider_id": -1},
    {"url": "https://example.org/pull/42"}, {"url": "https://github.com/acme/widget/pull/43"},
    {"provider_updated_at": NOW.replace(tzinfo=None)}, {"merged_at": NOW},
    {"state": "merged"}, {"state": "merged", "merged_at": NOW, "merge_commit_sha": "branch"},
    {"state": "merged", "merged_at": NOW + timedelta(seconds=1), "merge_commit_sha": MERGE},
])
def test_invalid_snapshots_are_rejected_before_persistence(over):
    with pytest.raises(ValueError):
        snapshot(**over)


@pytest.mark.parametrize("field,value", [
    ("merged", "false"), ("state", "merged"), ("updated_at", None),
    ("base", None), ("head", {}), ("number", None), ("merged_at", NOW.isoformat()),
])
def test_malformed_api_metadata_stays_unavailable(field, value):
    data = payload()
    data[field] = value
    with pytest.raises(ValueError):
        PullRequestSnapshot.from_github(data)


@respx.mock
async def test_provider_only_reads_requested_pr_and_checks_base_identity():
    route = respx.get(f"{API_ROOT}/repos/acme/widget/pulls/42").respond(200, json=payload())
    provider = GitHubProvider("test")
    try:
        assert await provider.fetch_pull_request(REPO, 42) == snapshot()
        assert route.call_count == 1
        assert respx.calls[0].request.method == "GET"
    finally:
        await provider.aclose()


@pytest.mark.parametrize("field,value", [
    ("number", 99), ("base", {"ref": "main", "repo": {"id": 2}}),
])
@respx.mock
async def test_provider_refuses_a_pr_from_another_repository_or_number(field, value):
    data = payload()
    data[field] = value
    if field == "number":
        data["html_url"] = f"https://github.com/acme/widget/pull/{value}"
    respx.get(f"{API_ROOT}/repos/acme/widget/pulls/42").respond(200, json=data)
    provider = GitHubProvider("test")
    try:
        with pytest.raises(ValueError, match="requested repository/number"):
            await provider.fetch_pull_request(REPO, 42)
    finally:
        await provider.aclose()
