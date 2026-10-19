"""The user-facing preview remains offline-capable and read-only in the database."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

import cadence.cli as cli
from cadence.detectors.base import EvidenceDraft, FindingDraft
from cadence.fixers.base import FixResult
from cadence.preview import CachePreview
from cadence.source import FileInventory, PreviewSource

PATH = ".github/workflows/ci.yml"
SHA = "a" * 40
CI = "on: push\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps: []\n"
RUNNER = CliRunner()


@pytest.fixture
def offline(tmp_path, monkeypatch):
    source = PreviewSource("acme/widget", FileInventory(SHA, frozenset({PATH}), True), {PATH: CI})
    snapshot = source.save(tmp_path / "sources")
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = {"id": 1}
    conn.execute.return_value.fetchall.return_value = [{
        "kind": "no_dependency_cache", "dedupe_key": f"no_dependency_cache:{PATH}:test",
        "suppress_scope": "rule_repo", "suppressed_reason": "intentional",
    }]
    manager = MagicMock()
    manager.__enter__.return_value = conn
    monkeypatch.setattr(cli, "connect", lambda: manager)
    monkeypatch.setattr(cli, "build_context", MagicMock(return_value=SimpleNamespace(runs=[1])))
    finding = FindingDraft(
        "no_dependency_cache", "waste", 3, .75, f"no_dependency_cache:{PATH}:test", "t", "v",
        [EvidenceDraft("run_history", run_ids=[1])],
    )
    preview = MagicMock(return_value=[
        CachePreview(finding, FixResult(None, "suppressed: intentional")),
    ])
    monkeypatch.setattr(cli, "preview_cache_fixes", preview)

    def no_network(*args, **kwargs):
        pytest.fail("offline preview tried to use GitHub")

    monkeypatch.setattr(cli, "GitHubProvider", no_network)
    monkeypatch.setattr(cli, "_require_token", no_network)
    return snapshot, conn, preview


def test_offline_preview_emits_json_and_never_writes_findings(offline, tmp_path):
    snapshot, conn, preview = offline
    original = snapshot.read_bytes()
    report = tmp_path / "previews.json"
    result = RUNNER.invoke(cli.app, ["fix", "preview", "acme/widget", "--snapshot", str(snapshot),
                                    "--json", str(report)])
    assert result.exit_code == 0, result.output
    assert "0 cache preview(s), 1 declined" in result.output
    assert "suppressed: intentional" in result.output
    payload = json.loads(report.read_text())
    assert payload["ref"] == SHA and payload["repo"] == "acme/widget"
    assert payload["previews"][0]["diff"] is None
    assert snapshot.read_bytes() == original
    queries = [call.args[0] for call in conn.execute.call_args_list]
    assert queries[0] == "SET TRANSACTION READ ONLY"
    assert all(q.startswith(("SELECT", "SET TRANSACTION READ ONLY")) for q in queries)
    assert preview.call_args.kwargs["suppressed"][0]["suppress_scope"] == "rule_repo"


def test_unknown_job_fails_with_an_explanation(offline):
    snapshot, _, preview = offline
    result = RUNNER.invoke(cli.app, ["fix", "preview", "acme/widget", "--snapshot", str(snapshot),
                                    "--job", "missing"])
    assert result.exit_code == 1 and "job key" in result.output
    preview.assert_not_called()


def test_saved_snapshot_and_live_ref_are_mutually_exclusive(offline):
    snapshot, _, _ = offline
    result = RUNNER.invoke(cli.app, ["fix", "preview", "acme/widget", "--snapshot", str(snapshot),
                                    "--ref", "main"])
    assert result.exit_code == 2


@pytest.mark.parametrize("repo", ["bad", "owner/name/extra", "/empty"])
def test_malformed_repository_is_rejected_before_io(repo, offline):
    snapshot, conn, _ = offline
    result = RUNNER.invoke(cli.app, ["fix", "preview", repo, "--snapshot", str(snapshot)])
    assert result.exit_code == 2
    conn.execute.assert_not_called()
