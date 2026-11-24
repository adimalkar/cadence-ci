"""Selection closes before HTTP; each successful attempt has its own transaction."""

import json
from contextlib import contextmanager

import pytest
from typer.testing import CliRunner

from cadence import cli
from cadence.providers.base import RateLimited
from test_job_refresh import REPO, RUN_ID, job, target


@pytest.fixture
def setup(monkeypatch):
    state = {"active": 0, "opens": 0, "closed": False, "saved": []}

    class Connection:
        def execute(self, *args):
            return self

        def fetchone(self):
            return dict(
                id=REPO.id,
                owner=REPO.owner,
                name=REPO.name,
                is_private=False,
                default_branch="main",
            )

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
            assert state["active"] == 0

        async def fetch_jobs(self, repo, run_id, *, attempt, max_pages):
            assert state["active"] == 0
            if run_id == RUN_ID + 1 and state.get("fail"):
                raise RateLimited(19)
            return [job(run_id=run_id)]

        async def aclose(self):
            state["closed"] = True

    def persist(conn, selected, jobs):
        assert state["active"] == 1
        state["saved"].append(selected.run_id)
        return {"status": "refreshed", "jobs_written": 1, "steps_written": 1}

    monkeypatch.setattr(cli, "connect", connect)
    monkeypatch.setattr(
        cli, "select_targets", lambda conn, repo_id, ids, **kwargs: [target(n) for n in ids]
    )
    monkeypatch.setattr(cli, "_require_token", lambda: "test-token")
    monkeypatch.setattr(cli, "GitHubProvider", Provider)
    monkeypatch.setattr(cli, "persist_jobs", persist)
    return state


@pytest.mark.parametrize("fail", [False, True])
def test_success_and_partial_failure_write_json(setup, tmp_path, fail):
    setup["fail"] = fail
    path = tmp_path / "repair.json"
    args = [
        "refresh-jobs",
        "repair-test/widget",
        "--run",
        str(RUN_ID),
        "--run",
        str(RUN_ID + 1),
        "--json",
        str(path),
    ]
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == int(fail), result.output
    assert setup["closed"] and setup["active"] == 0
    assert setup["saved"] == ([RUN_ID] if fail else [RUN_ID, RUN_ID + 1])
    assert setup["opens"] == (2 if fail else 3)
    report = json.loads(path.read_text())
    assert report["remaining_attempts"] == ([{"run_id": RUN_ID + 1, "attempt": 1}] if fail else [])
    if fail:
        assert report["stopped"]["retry_after_seconds"] == 19


@pytest.mark.parametrize(
    "args",
    [
        ["bad-repo"],
        ["a/b"],
        ["a/b", "--run", "0"],
        ["a/b", "--run", "1", "--max-pages", "101"],
        ["a/b", "--run", "1", "--attempt", "0"],
    ],
)
def test_bad_selection_never_opens_database(setup, args):
    result = CliRunner().invoke(cli.app, ["refresh-jobs", *args])
    assert result.exit_code == 2, result.output
    assert setup["opens"] == 0
