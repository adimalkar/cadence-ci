"""A partial tree, network failure or moving branch cannot prove file absence."""

from __future__ import annotations

import base64
import json

import pytest
import respx

from cadence.models import Repo
from cadence.providers.github import API_ROOT, GitHubProvider
from cadence.source import FileInventory, PreviewSource, SourceUnavailable, capture_source

REPO = Repo(id=1, owner="acme", name="widget", is_private=False, default_branch="main")
SHA = "b" * 40
TREE = "c" * 40
PATH = ".github/workflows/ci.yml"
CI = """\
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - uses: actions/setup-python@v5
      - uses: ./.github/actions/prepare
      - run: pip install -r requirements.txt
"""
MANIFEST = """\
inputs:
  cache:
  cache-dependency-path:
runs:
  using: node20
  main: dist/index.js
"""


@respx.mock
async def test_inventory_resolves_the_commit_and_excludes_symlinks_and_submodules():
    respx.get(f"{API_ROOT}/repos/acme/widget/commits/main").respond(
        200, json={"sha": SHA, "commit": {"tree": {"sha": TREE}}},
    )
    request = respx.get(f"{API_ROOT}/repos/acme/widget/git/trees/{TREE}",
                        params={"recursive": "1"}).respond(
        200, json={"truncated": False, "tree": [
            {"path": "requirements.txt", "type": "blob", "mode": "100644"},
            {"path": "script.sh", "type": "blob", "mode": "100755"},
            {"path": "linked.txt", "type": "blob", "mode": "120000"},
            {"path": "submodule", "type": "commit", "mode": "160000"},
        ]},
    )
    provider = GitHubProvider("test")
    try:
        result = await provider.fetch_file_inventory(REPO)
        assert result.ref == SHA and result.complete
        assert result.paths == {"requirements.txt", "script.sh"}
        assert request.called
    finally:
        await provider.aclose()


@pytest.mark.parametrize("payload", [
    {"truncated": True, "tree": []}, {"tree": []}, {"truncated": False},
])
@respx.mock
async def test_truncated_or_malformed_tree_is_never_reported_as_empty(payload):
    respx.get(f"{API_ROOT}/repos/acme/widget/commits/main").respond(
        200, json={"sha": SHA, "commit": {"tree": {"sha": TREE}}},
    )
    respx.get(f"{API_ROOT}/repos/acme/widget/git/trees/{TREE}").respond(200, json=payload)
    provider = GitHubProvider("test")
    try:
        with pytest.raises(SourceUnavailable):
            await provider.fetch_file_inventory(REPO)
    finally:
        await provider.aclose()


@respx.mock
async def test_non_utf8_workflow_is_unreadable_rather_than_lossily_edited():
    respx.get(f"{API_ROOT}/repos/acme/widget/contents/{PATH}").respond(
        200, json={"encoding": "base64", "content": base64.b64encode(b"name: \xff").decode()},
    )
    provider = GitHubProvider("test")
    try:
        assert await provider.fetch_text_file(REPO, PATH, ref=SHA) is None
    finally:
        await provider.aclose()


class Files:
    def __init__(self, *, complete=True, package_failure=False, missing_workflow=False,
                 ignore=False):
        self.complete, self.package_failure = complete, package_failure
        self.missing_workflow, self.ignore = missing_workflow, ignore
        self.calls = []

    async def fetch_file_inventory(self, repo, ref=None):
        return FileInventory(SHA, frozenset({PATH, "requirements.txt", "package.json"}
                             | ({".cadenceignore"} if self.ignore else set())), self.complete)

    async def fetch_text_file(self, repo, path, *, ref=None):
        self.calls.append((repo.owner, repo.name, path, ref))
        if repo.owner == "acme":
            assert ref == SHA  # even if `main` moved after the inventory call
        if path == PATH:
            return None if self.missing_workflow else CI
        if path == "package.json":
            if self.package_failure:
                raise RuntimeError("502")
            return '{"name":"widget"}'
        if repo.owner == "actions":
            assert ref == "v5"
            return MANIFEST
        if path.endswith("prepare/action.yml"):
            return ("runs:\n  using: composite\n  steps:\n"
                    "    - run: echo prepare\n      shell: bash\n")
        return None


async def test_every_local_file_and_action_uses_the_same_immutable_ref(tmp_path):
    provider = Files()
    source = await capture_source(provider, REPO, ref="main")
    assert source.workflows == {PATH: CI}
    assert source.package_json.fetched
    assert source.actions["actions/setup-python@v5"].inputs == {"cache", "cache-dependency-path"}
    assert source.actions["./.github/actions/prepare"].is_composite
    assert all(call[3] == SHA for call in provider.calls if call[0] == "acme")
    saved = source.save(tmp_path)
    restored = PreviewSource.load(saved)
    assert restored == source
    assert saved.parent == tmp_path / "acme__widget"


async def test_manifest_request_failure_stays_unknown():
    source = await capture_source(Files(package_failure=True), REPO)
    assert not source.package_json.fetched


async def test_missing_workflow_in_a_known_tree_aborts_capture():
    with pytest.raises(SourceUnavailable, match="could not read"):
        await capture_source(Files(missing_workflow=True), REPO)


async def test_partial_inventory_does_not_start_file_requests():
    provider = Files(complete=False)
    with pytest.raises(SourceUnavailable):
        await capture_source(provider, REPO)
    assert provider.calls == []


async def test_unreadable_ignore_policy_aborts_instead_of_unsuppressing():
    with pytest.raises(SourceUnavailable, match="suppression policy"):
        await capture_source(Files(ignore=True), REPO)


def test_unknown_snapshot_versions_or_unpinned_refs_are_refused(tmp_path):
    path = tmp_path / "snapshot.json"
    for data in [{"version": 2, "ref": SHA}, {"version": 1, "ref": "main"}]:
        path.write_text(json.dumps(data))
        with pytest.raises(SourceUnavailable):
            PreviewSource.load(path)
