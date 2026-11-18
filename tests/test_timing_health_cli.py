"""Offline CLI: validation, complete totals under truncation and opt-in exit policy."""

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
        raise AssertionError("offline report attempted network/token")

    monkeypatch.setattr(cli, "connect", connect)
    monkeypatch.setattr(cli, "_require_token", forbidden)
    monkeypatch.setattr(cli, "GitHubProvider", forbidden)
    return state


@pytest.mark.parametrize("fail", [False, True])
@pytest.mark.parametrize("severity", ["unknown", "info"])
def test_report_and_optional_failure_keep_json_after_connection_closes(
    setup, monkeypatch, tmp_path, fail, severity
):
    def report(*args, **kwargs):
        return {
            "selected_run_ids": [1],
            "counts": {"incomplete": 1},
            "runs": [
                {
                    "run_id": 1,
                    "attempt": 1,
                    "state": "incomplete",
                    "issue_counts": {"no_current_attempt_jobs": 1},
                    "issues": [],
                }
            ],
            "severity_counts": {severity: 1},
            "details_truncated": True,
            "next_before": 1,
            "meaning": "Observed consistency is not complete coverage.",
        }

    monkeypatch.setattr(cli, "timing_health", report)
    path = tmp_path / "timing.json"
    args = ["timing-health", "a/b", "--max-issues", "0", "--json", str(path)]
    if fail:
        args.append("--fail-on-issues")
    result = runner.invoke(cli.app, args)
    assert result.exit_code == int(fail and severity == "unknown"), result.output
    assert setup["closed"]
    assert json.loads(path.read_text())["severity_counts"] == {severity: 1}
    assert "totals and run states include omitted details" in result.output
    assert "Next page: --before 1" in result.output


@pytest.mark.parametrize(
    "args",
    [
        ["bad-repo"],
        ["a/b", "--run", "0"],
        ["a/b", "--limit", "501"],
        ["a/b", "--max-issues", "2001"],
        ["a/b", "--run", "1", "--before", "2"],
        ["a/b", "--run", "1", "--run", "2", "--limit", "1"],
    ],
)
def test_invalid_selection_never_opens_database(setup, args):
    result = runner.invoke(cli.app, ["timing-health", *args])
    assert result.exit_code == 2, result.output
    assert setup["opens"] == 0
