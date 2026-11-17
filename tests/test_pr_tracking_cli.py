"""Registration is explicit; inspecting stored run links is fully offline and read-only."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from typer.testing import CliRunner

import cadence.cli as cli
from cadence.providers.base import NotFound

RUNNER = CliRunner()


@pytest.fixture
def database(monkeypatch):
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = {
        "id": 1, "owner": "acme", "name": "widget", "is_private": False,
        "default_branch": "main",
    }
    manager = MagicMock()
    manager.__enter__.return_value = conn
    monkeypatch.setattr(cli, "connect", lambda: manager)
    return conn


def test_link_listing_is_offline_read_only_and_exports_provenance(database, monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("offline run listing requested network or credentials")
    monkeypatch.setattr(cli, "GitHubProvider", forbidden)
    monkeypatch.setattr(cli, "_require_token", forbidden)
    result_data = {
        "pull_request": {"last_synced_at": "2026-11-17T00:00:00Z"},
        "runs": [{"run_id": 1, "provenance": "api_pr_number", "workflow_path": "ci.yml",
                  "conclusion": "success", "status": "completed"}], "truncated": True,
    }
    lookup = MagicMock(return_value=result_data)
    monkeypatch.setattr(cli, "linked_runs", lookup)
    path = tmp_path / "links.json"
    result = RUNNER.invoke(cli.app, ["fix", "runs", "acme/widget", "42",
                                    "--limit", "1", "--json", str(path)])
    assert result.exit_code == 0, result.output
    assert "1 stored run link(s)" in result.output and "more available" in result.output
    assert json.loads(path.read_text()) == result_data
    lookup.assert_called_once_with(database, 1, 42, limit=1)
    queries = [call.args[0] for call in database.execute.call_args_list]
    assert queries[0] == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
    assert all(q.startswith(("SELECT", "SET TRANSACTION")) for q in queries)


def test_tracking_reads_api_once_and_explicitly_registers_one_finding(database, monkeypatch):
    provider = SimpleNamespace(fetch_pull_request=AsyncMock(
        return_value=SimpleNamespace(state="open")), aclose=AsyncMock())
    monkeypatch.setattr(cli, "_require_token", lambda: "test")
    monkeypatch.setattr(cli, "GitHubProvider", lambda token: provider)
    register = MagicMock()
    monkeypatch.setattr(cli, "track_pull_request", register)
    result = RUNNER.invoke(cli.app, ["fix", "track", "acme/widget", "42",
                                    "--finding", "no_dependency_cache:ci.yml:build"])
    assert result.exit_code == 0, result.output
    assert "Tracked acme/widget#42: open" in result.output
    assert provider.fetch_pull_request.await_count == 1
    provider.aclose.assert_awaited_once()
    register.assert_called_once_with(database, provider.fetch_pull_request.return_value,
                                     "no_dependency_cache:ci.yml:build")


def test_unreadable_pr_leaves_no_registration_and_closes_client(database, monkeypatch):
    provider = SimpleNamespace(fetch_pull_request=AsyncMock(side_effect=NotFound("missing")),
                               aclose=AsyncMock())
    monkeypatch.setattr(cli, "_require_token", lambda: "test")
    monkeypatch.setattr(cli, "GitHubProvider", lambda token: provider)
    register = MagicMock()
    monkeypatch.setattr(cli, "track_pull_request", register)
    result = RUNNER.invoke(cli.app, ["fix", "track", "acme/widget", "42", "--finding", "key"])
    assert result.exit_code == 1 and "Could not read PR metadata" in result.output
    register.assert_not_called()
    provider.aclose.assert_awaited_once()


@pytest.mark.parametrize("arguments", [
    ["fix", "track", "bad", "42", "--finding", "key"],
    ["fix", "track", "acme/widget", "0", "--finding", "key"],
    ["fix", "track", "acme/widget", "42"],
    ["fix", "runs", "/empty", "42"],
    ["fix", "runs", "acme/widget", "42", "--limit", "0"],
    ["fix", "runs", "acme/widget", "42", "--limit", "5001"],
])
def test_invalid_arguments_fail_before_io(monkeypatch, arguments):
    monkeypatch.setattr(cli, "connect", lambda: pytest.fail("invalid arguments reached DB"))
    assert RUNNER.invoke(cli.app, arguments).exit_code == 2
