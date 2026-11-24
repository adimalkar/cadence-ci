"""The command closes DB transactions before HTTP and reports resumable failures."""
import json
from contextlib import contextmanager

import pytest
from typer.testing import CliRunner

from cadence import cli
from cadence.providers.base import RateLimited
from test_run_refresh import REPO, snapshot

runner = CliRunner()


@pytest.fixture
def environment(monkeypatch):
    state = {"active": 0, "opens": 0, "closed": False, "selected": [100, 99], "saved": []}

    class Connection:
        def execute(self, query, args=None):
            return self

        def fetchone(self):
            return {"id": REPO.id, "owner": REPO.owner, "name": REPO.name,
                    "is_private": False, "default_branch": "main"}

    @contextmanager
    def connect():
        state["active"] += 1
        state["opens"] += 1
        try:
            yield Connection()
        finally:
            state["active"] -= 1

    class Provider:
        def __init__(self, token):
            assert token == "test-token"
            assert state["active"] == 0

        async def fetch_run(self, repo, run_id):
            assert state["active"] == 0
            if run_id == state.get("fail"):
                raise RateLimited(17)
            return snapshot(run_id)

        async def aclose(self):
            state["closed"] = True

    def persist(conn, run):
        assert state["active"] == 1
        state["saved"].append(run.id)
        return "refreshed"

    monkeypatch.setattr(cli, "connect", connect)
    monkeypatch.setattr(cli, "_require_token", lambda: "test-token")
    monkeypatch.setattr(cli, "GitHubProvider", Provider)
    monkeypatch.setattr(cli, "select_run_ids", lambda *a, **kw: {
        "run_ids": state["selected"], "next_before": 99})
    monkeypatch.setattr(cli, "refresh_stored_run", persist)
    return state


def test_separate_transactions_and_json(environment, tmp_path):
    path = tmp_path / "refresh.json"
    result = runner.invoke(cli.app, ["refresh-runs", "acme/widget", "--json", str(path)])
    assert result.exit_code == 0, result.output
    assert environment["saved"] == [100, 99]
    assert environment["opens"] == 3 and environment["active"] == 0
    assert environment["closed"]
    output = json.loads(path.read_text())
    assert output["counts"] == {"refreshed": 2}
    assert output["next_before"] == 99


def test_partial_failure_still_writes_json_and_closes_provider(environment, tmp_path):
    environment["fail"] = 99
    path = tmp_path / "refresh.json"
    result = runner.invoke(cli.app, ["refresh-runs", "acme/widget", "--json", str(path)])
    assert result.exit_code == 1, result.output
    assert environment["saved"] == [100]
    assert environment["closed"] and environment["active"] == 0
    output = json.loads(path.read_text())
    assert output["remaining_run_ids"] == [99]
    assert output["stopped"]["retry_after_seconds"] == 17
    assert "retry pending IDs first" in result.output


def test_empty_selection_needs_no_token_or_provider(environment, monkeypatch):
    environment["selected"] = []

    def forbidden():
        raise AssertionError("empty selection requested token")

    monkeypatch.setattr(cli, "_require_token", forbidden)
    result = runner.invoke(cli.app, ["refresh-runs", "acme/widget"])
    assert result.exit_code == 0, result.output
    assert environment["opens"] == 1 and not environment["closed"]


@pytest.mark.parametrize("args", [
    ["bad-repo"], ["a/b", "--run", "0"], ["a/b", "--before", str(2**63)],
    ["a/b", "--limit", "501"], ["a/b", "--run", "1", "--before", "2"],
    ["a/b", "--run", "1", "--run", "2", "--limit", "1"],
])
def test_invalid_arguments_do_not_open_database(environment, args):
    result = runner.invoke(cli.app, ["refresh-runs", *args])
    assert result.exit_code == 2, result.output
    assert environment["opens"] == 0
