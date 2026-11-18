"""Offline coverage report: no token/network and no optimistic legacy backfill."""
import json
from contextlib import contextmanager

import pytest
from typer.testing import CliRunner

from cadence import cli

runner = CliRunner()


@pytest.fixture
def setup(monkeypatch):
    state = {"opens": 0, "closed": False}

    class Connection:
        def execute(self, sql, params=None):
            if sql.startswith("SET"):
                assert "REPEATABLE READ READ ONLY" in sql
            return self

        def fetchone(self):
            return {"id": 42}

    @contextmanager
    def connect():
        state["opens"] += 1
        try:
            yield Connection()
        finally:
            state["closed"] = True

    def forbidden(*args, **kwargs):
        raise AssertionError("offline coverage requested API/token")

    monkeypatch.setattr(cli, "connect", connect)
    monkeypatch.setattr(cli, "GitHubProvider", forbidden)
    monkeypatch.setattr(cli, "_require_token", forbidden)
    return state


def test_unknown_evidence_is_reported_without_network(setup, monkeypatch, tmp_path):
    def coverage(conn, repo_id, **kwargs):
        assert repo_id == 42 and kwargs == {"run_ids": [101], "limit": 50}
        return {"runs": [{"run_id": 101, "attempt": 1, "coverage": "unknown",
                          "stored_count": 2, "expected_count": None}],
                "counts": {"unknown": 1}, "truncated": False, "meaning": "historical receipt only"}

    monkeypatch.setattr(cli, "job_coverage", coverage)
    path = tmp_path / "coverage.json"
    result = runner.invoke(cli.app, ["job-coverage", "a/b", "--run", "101", "--json", str(path)])
    assert result.exit_code == 0, result.output
    assert json.loads(path.read_text())["counts"] == {"unknown": 1}
    assert "unknown" in result.output
    assert setup["closed"]


@pytest.mark.parametrize("args", [
    ["bad-repo"], ["a/b", "--run", "0"], ["a/b", "--limit", "501"],
    ["a/b", "--run", "1", "--run", "2", "--limit", "1"],
])
def test_invalid_selection_never_opens_db(setup, args):
    result = runner.invoke(cli.app, ["job-coverage", *args])
    assert result.exit_code == 2, result.output
    assert setup["opens"] == 0


def test_unknown_repo_closes_connection_and_reports_error(setup, monkeypatch):
    @contextmanager
    def connect():
        class Missing:
            def execute(self, *args):
                return self

            def fetchone(self):
                return None

        yield Missing()

    monkeypatch.setattr(cli, "connect", connect)
    result = runner.invoke(cli.app, ["job-coverage", "missing/repo"])
    assert result.exit_code == 1
    assert "not ingested" in result.output
