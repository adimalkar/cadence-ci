"""Offline query output must preserve provenance, uncertainty and pagination limits."""

import json
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

import cadence.cli as cli

RUNNER = CliRunner()


@pytest.fixture
def database(monkeypatch):
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = {"id": 1}
    manager = MagicMock()
    manager.__enter__.return_value = conn
    monkeypatch.setattr(cli, "connect", lambda: manager)
    return conn


def test_pr_query_is_offline_read_only_and_exports_all_evidence(database, monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("offline association query requested network or credentials")
    monkeypatch.setattr(cli, "GitHubProvider", forbidden)
    monkeypatch.setattr(cli, "_require_token", forbidden)
    data = {
        "repo_id": 1, "number": 43, "absence_is_unknown": True, "truncated": True,
        "runs": [{"run_id": 1, "source": "provider", "workflow_path": "ci.yml",
                  "status": "completed", "conclusion": "failure"}],
    }
    query = MagicMock(return_value=data)
    monkeypatch.setattr(cli, "runs_for_pr", query)
    path = tmp_path / "associations.json"
    result = RUNNER.invoke(cli.app, ["pr-runs", "acme/widget", "43",
                                    "--limit", "1", "--json", str(path)])
    assert result.exit_code == 0, result.output
    assert "1 recorded run association(s)" in result.output
    assert "more available" in result.output and "remain unknown" in result.output
    assert json.loads(path.read_text()) == data
    query.assert_called_once_with(database, 1, 43, limit=1)
    queries = [call.args[0] for call in database.execute.call_args_list]
    assert queries[0] == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
    assert all(q.startswith(("SET TRANSACTION", "SELECT")) for q in queries)


def test_unknown_repo_does_not_query_associations(database, monkeypatch):
    database.execute.return_value.fetchone.return_value = None
    query = MagicMock()
    monkeypatch.setattr(cli, "runs_for_pr", query)
    result = RUNNER.invoke(cli.app, ["pr-runs", "acme/missing", "43"])
    assert result.exit_code == 1 and "not ingested" in result.output
    query.assert_not_called()


@pytest.mark.parametrize("arguments", [
    ["bad", "42"], ["/empty", "42"], ["a/b/c", "42"],
    ["a/b", "0"], ["a/b", "-1"], ["a/b", "2147483648"],
    ["a/b", "42", "--limit", "0"], ["a/b", "42", "--limit", "5001"],
])
def test_invalid_arguments_fail_before_io(monkeypatch, arguments):
    monkeypatch.setattr(cli, "connect", lambda: pytest.fail("invalid arguments reached DB"))
    assert RUNNER.invoke(cli.app, ["pr-runs", *arguments]).exit_code == 2
