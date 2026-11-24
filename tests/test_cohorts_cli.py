"""CLI validation happens before DB access; a declined result still has JSON evidence."""
import json
from contextlib import contextmanager

import pytest
from typer.testing import CliRunner

from cadence import cli
from cadence.cohorts import compare_cohorts
from test_cohorts import WORKFLOW, cohorts

runner = CliRunner()


@pytest.mark.parametrize("args", [
    ["bad-repo"], ["a/b"], ["a/b", "--before-run", "1"],
    ["a/b", "--before-run", "1", "--after-run", "1"],
    ["a/b", "--before-run", "0", "--after-run", "2"],
    ["a/b", "--before-run", "1", "--after-run", str(2**63)],
    ["a/b", "--before-run", "1", "--after-run", "2", "--min-samples", "1"],
])
def test_invalid_arguments_never_open_db(monkeypatch, args):
    def forbidden():
        raise AssertionError("invalid selection opened database")

    monkeypatch.setattr(cli, "connect", forbidden)
    result = runner.invoke(cli.app, ["compare-runs", *args, "--workflow", WORKFLOW])
    assert result.exit_code == 2, result.output


def test_no_eligible_comparison_writes_json_and_exits_nonzero(monkeypatch, tmp_path):
    before, after = cohorts()
    report = compare_cohorts(before, after, WORKFLOW)  # below the default floor

    class Connection:
        def execute(self, query, params=None):
            return self

        def fetchone(self):
            return {"id": 7}

    @contextmanager
    def connect():
        yield Connection()

    monkeypatch.setattr(cli, "connect", connect)
    monkeypatch.setattr(cli, "compare_stored_runs", lambda *a, **kw: report)
    path = tmp_path / "declined.json"
    result = runner.invoke(cli.app, ["compare-runs", "a/b", "--workflow", WORKFLOW,
                                    "--before-run", "1", "--after-run", "2",
                                    "--json", str(path)])
    assert result.exit_code == 1, result.output
    assert json.loads(path.read_text())["declined_profiles"][0]["reason"] == "insufficient_samples"
    assert "excluded run(s)" in result.output and "insufficient_samples" in result.output
