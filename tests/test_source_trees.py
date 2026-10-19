"""Oversized Git trees must be complete before source absence can be inferred."""

import base64

import pytest
import respx

from cadence.models import Repo
from cadence.providers import github
from cadence.providers.github import API_ROOT, GitHubProvider
from cadence.source import SourceUnavailable, capture_source

REPO = Repo(id=1, owner="acme", name="widget", is_private=False, default_branch="main")
SHA = "b" * 40
TREE = "c" * 40
PATH = ".github/workflows/ci.yml"
BASE = f"{API_ROOT}/repos/acme/widget"
CHILD = "d" * 40
LEAF = "e" * 40


def blob(path, mode="100644"):
    return {"path": path, "type": "blob", "mode": mode}


def subtree(path, sha=CHILD):
    return {"path": path, "type": "tree", "mode": "040000", "sha": sha}


def tree_response(sha, items, *, recursive=True, truncated=False):
    return respx.get(f"{BASE}/git/trees/{sha}",
                     params={"recursive": "1"} if recursive else {}).respond(
        200, json={"truncated": truncated, "tree": items},
    )


@pytest.fixture
async def provider():
    client = GitHubProvider("test")
    try:
        yield client
    finally:
        await client.aclose()


def commit(ref="main"):
    return respx.get(f"{BASE}/commits/{ref}").respond(
        200, json={"sha": SHA, "commit": {"tree": {"sha": TREE}}},
    )


@respx.mock
async def test_split_truncated_trees_recursively_without_using_partial_entries(provider):
    resolved = commit()
    tree_response(TREE, [blob("partial-root.txt")], truncated=True)
    shallow = tree_response(TREE, [blob("package.json"), subtree("packages"),
                                   blob("linked", "120000"),
                                   {"path": "vendor", "type": "commit", "mode": "160000"}],
                            recursive=False)
    tree_response(CHILD, [blob("partial-child.txt")], truncated=True)
    tree_response(CHILD, [blob("build.sh", "100755"), subtree("widget", LEAF)],
                  recursive=False)
    tree_response(LEAF, [blob("package-lock.json"), blob("linked", "120000")])

    result = await provider.fetch_file_inventory(REPO)

    assert result.complete and result.ref == SHA
    assert result.paths == {"package.json", "packages/build.sh",
                            "packages/widget/package-lock.json"}
    assert resolved.call_count == 1  # no branch re-resolution during traversal
    assert "recursive" not in shallow.calls[0].request.url.params
    assert len(respx.calls) == 6


@respx.mock
async def test_complete_recursive_tree_does_not_fetch_each_directory(provider):
    commit()
    tree_response(TREE, [subtree("packages"), blob("packages/requirements.txt")])
    result = await provider.fetch_file_inventory(REPO)
    assert result.complete and result.paths == {"packages/requirements.txt"}
    assert len(respx.calls) == 2


@respx.mock
async def test_identical_subtree_is_fetched_once_but_expanded_at_both_paths(provider):
    commit()
    tree_response(TREE, [], truncated=True)
    tree_response(TREE, [subtree("one"), subtree("two")], recursive=False)
    child = tree_response(CHILD, [blob("requirements.txt")])
    result = await provider.fetch_file_inventory(REPO)
    assert result.paths == {"one/requirements.txt", "two/requirements.txt"}
    assert result.complete and child.call_count == 1


@pytest.mark.parametrize("payload", [
    {"truncated": True, "tree": [blob("requirements.txt")]},
    {"tree": []}, {"truncated": False}, {"truncated": 0, "tree": []}, [],
])
@respx.mock
async def test_incomplete_shallow_response_never_returns_an_inventory(provider, payload):
    commit()
    tree_response(TREE, [blob("partial.txt")], truncated=True)
    respx.get(f"{BASE}/git/trees/{TREE}", params={}).respond(200, json=payload)
    with pytest.raises(SourceUnavailable):
        await provider.fetch_file_inventory(REPO)


@respx.mock
async def test_unavailable_subtree_cannot_make_a_lockfile_look_absent(provider):
    commit()
    tree_response(TREE, [], truncated=True)
    tree_response(TREE, [blob("package.json"), subtree("packages")], recursive=False)
    respx.get(f"{BASE}/git/trees/{CHILD}").respond(404)
    with pytest.raises(SourceUnavailable, match="subtree is unavailable"):
        await provider.fetch_file_inventory(REPO)


@pytest.mark.parametrize("limit, succeeds", [(3, False), (4, True)])
@respx.mock
async def test_tree_request_budget_includes_recursive_and_shallow_reads(
    provider, monkeypatch, limit, succeeds,
):
    monkeypatch.setattr(github, "MAX_TREE_REQUESTS", limit)
    commit()
    tree_response(TREE, [], truncated=True)
    tree_response(TREE, [subtree("packages"), subtree("other", LEAF)], recursive=False)
    tree_response(CHILD, [blob("requirements.txt")])
    tree_response(LEAF, [])
    if succeeds:
        assert (await provider.fetch_file_inventory(REPO)).paths == {"packages/requirements.txt"}
    else:
        with pytest.raises(SourceUnavailable, match="lookup budget exhausted"):
            await provider.fetch_file_inventory(REPO)
    assert len(respx.calls) == limit + 1  # plus the single commit read


@pytest.mark.parametrize("limit, succeeds", [(4, False), (5, True)])
@respx.mock
async def test_entry_budget_counts_partial_responses_and_reused_expansions(
    provider, monkeypatch, limit, succeeds,
):
    monkeypatch.setattr(github, "MAX_TREE_ENTRIES", limit)
    commit()
    tree_response(TREE, [blob("discarded.txt")], truncated=True)
    tree_response(TREE, [subtree("one"), subtree("two")], recursive=False)
    tree_response(CHILD, [blob("requirements.txt")])
    if succeeds:
        assert len((await provider.fetch_file_inventory(REPO)).paths) == 2
    else:
        with pytest.raises(SourceUnavailable, match="entry budget exhausted"):
            await provider.fetch_file_inventory(REPO)


@pytest.mark.parametrize("entry", [
    None, blob(""), blob("/absolute"), blob("../requirements.txt"), blob("a//b"),
    blob("./requirements.txt"), blob("a/../b"), blob("a", "999999"),
    {"path": "a", "type": "unknown", "mode": "100644"}, subtree("a", "main"),
])
@respx.mock
async def test_malformed_complete_entries_remain_unknown(provider, entry):
    commit()
    tree_response(TREE, [entry])
    with pytest.raises(SourceUnavailable):
        await provider.fetch_file_inventory(REPO)


@respx.mock
async def test_duplicate_paths_remain_unknown(provider):
    commit()
    tree_response(TREE, [blob("requirements.txt"), blob("requirements.txt")])
    with pytest.raises(SourceUnavailable, match="invalid path"):
        await provider.fetch_file_inventory(REPO)


@respx.mock
async def test_nonrecursive_listing_cannot_smuggle_nested_paths(provider):
    commit()
    tree_response(TREE, [], truncated=True)
    tree_response(TREE, [blob("packages/requirements.txt")], recursive=False)
    with pytest.raises(SourceUnavailable, match="invalid path"):
        await provider.fetch_file_inventory(REPO)


@respx.mock
async def test_cycle_in_malformed_subtree_response_stops_traversal(provider):
    commit()
    tree_response(TREE, [], truncated=True)
    tree_response(TREE, [subtree("cycle", TREE)], recursive=False)
    with pytest.raises(SourceUnavailable, match="cycle"):
        await provider.fetch_file_inventory(REPO)
    assert len(respx.calls) == 3


@pytest.mark.parametrize("payload", [
    [], {"sha": SHA, "commit": None}, {"sha": None},
    {"sha": SHA, "commit": {"tree": {"sha": "main"}}},
])
@respx.mock
async def test_commit_must_identify_both_immutable_commit_and_tree(provider, payload):
    respx.get(f"{BASE}/commits/main").respond(200, json=payload)
    with pytest.raises(SourceUnavailable, match="immutable tree"):
        await provider.fetch_file_inventory(REPO)
    assert len(respx.calls) == 1


@respx.mock
async def test_fallback_capture_reads_workflow_bytes_at_resolved_commit(provider):
    commit("release")
    tree_response(TREE, [], truncated=True)
    tree_response(TREE, [subtree(".github")], recursive=False)
    tree_response(CHILD, [subtree("workflows", LEAF), blob("workflows/ci.yml")])
    workflow = "on: push\njobs: {}\n"
    file_read = respx.get(f"{BASE}/contents/{PATH}", params={"ref": SHA}).respond(
        200, json={"encoding": "base64", "content": base64.b64encode(workflow.encode()).decode()},
    )
    source = await capture_source(provider, REPO, ref="release")
    assert source.workflows == {PATH: workflow}
    assert source.inventory.complete and source.inventory.ref == SHA
    assert file_read.called
