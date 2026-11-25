"""The analysis contract (schema cadence.analysis/1): read-only, pinned, bounded."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg.rows import dict_row
from typer.testing import CliRunner

from cadence import analysis as an
from cadence.db.conn import apply_migrations
from cadence.ingest import upsert_job, upsert_repo, upsert_run
from cadence.models import Job, Repo, Run, Step
from cadence.source import FileInventory, PreviewSource

TEST_DB = os.environ.get("CADENCE_TEST_DATABASE_URL", "postgresql://localhost/cadence_test")


def _db_available() -> bool:
    try:
        with psycopg.connect(TEST_DB, connect_timeout=2):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="no test database")

SHA = "a" * 40
CI = ".github/workflows/ci.yml"
YAML = """\
on: push
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - name: Test
        run: pytest
      - name: Lint
        run: ruff check
"""
REPO = Repo(id=9191, owner="acme", name="contract", is_private=False, default_branch="main")


def source(ignore: str | None = None) -> PreviewSource:
    paths = {CI} | ({".cadenceignore"} if ignore else set())
    return PreviewSource("acme/contract", FileInventory(SHA, frozenset(paths), complete=True),
                         {CI: YAML}, ignore_file=ignore)


@pytest.fixture(scope="module")
def schema():
    apply_migrations(TEST_DB)


@pytest.fixture
def seeded(schema):
    """25 runs whose `Test` step takes ten minutes: one long-tail finding."""
    t0 = datetime.now(UTC) - timedelta(days=5)
    with psycopg.connect(TEST_DB, row_factory=dict_row) as conn:
        upsert_repo(conn, REPO)
        for i in range(25):
            start = t0 + timedelta(hours=i)
            upsert_run(conn, Run(
                id=91000 + i, repo_id=REPO.id, workflow_id=1, workflow_path=CI,
                workflow_name="CI", run_number=i, run_attempt=1, event="push",
                status="completed", conclusion="success", head_sha=f"s{i}",
                head_branch="main", created_at=start, started_at=start,
                updated_at=start + timedelta(minutes=11)))
            upsert_job(conn, Job(
                id=910000 + i, run_id=91000 + i, name="build", name_base="build",
                status="completed", conclusion="success", runner_labels=["ubuntu-latest"],
                runner_group=None, created_at=start, started_at=start,
                completed_at=start + timedelta(minutes=11), attempt=1, steps=[
                    Step(1, "Test", "completed", "success", start,
                         start + timedelta(minutes=10)),
                    Step(2, "Lint", "completed", "success", start + timedelta(minutes=10),
                         start + timedelta(minutes=10, seconds=5)),
                ]), REPO.id)
        conn.commit()
    yield
    with psycopg.connect(TEST_DB) as conn:
        conn.execute("DELETE FROM finding WHERE repo_id = %s", (REPO.id,))
        conn.execute("DELETE FROM job WHERE repo_id = %s", (REPO.id,))
        conn.execute("DELETE FROM run WHERE repo_id = %s", (REPO.id,))
        conn.execute("DELETE FROM repo WHERE id = %s", (REPO.id,))
        conn.commit()


def run_analysis(src: PreviewSource | None = None) -> an.Analysis:
    with psycopg.connect(TEST_DB, row_factory=dict_row) as conn:
        return an.analyze(conn, src or source(), window_days=30, source_path="snap.json")


class TestSummary:
    def test_versioned_pinned_and_compact(self, seeded):
        view = an.summary_view(run_analysis())
        assert view["schema_version"] == "cadence.analysis/1"
        assert view["read_only"] is True
        assert view["source"] == {"sha": SHA, "workflows": [CI], "snapshot": "snap.json"}
        assert view["coverage"]["runs_with_jobs"] == 25
        assert view["coverage"]["runs_with_step_timings"] == 25
        assert view["coverage"]["run_limit_reached"] is False
        finding, = view["findings"]
        assert finding["kind"] == "long_tail_step"
        assert finding["id"] == an.finding_id("acme/contract", "long_tail_step:" + CI
                                              + ":build:Test")
        # Handles, not bodies: the summary never carries payloads or run id lists.
        assert all("payload" not in e and "run_ids" not in e for e in finding["evidence"])
        assert finding["evidence"][0]["id"] == finding["id"] + ".e0"
        assert len(json.dumps(view)) < 4000

    def test_same_source_and_history_give_the_same_analysis_id(self, seeded):
        assert run_analysis().analysis_id == run_analysis().analysis_id

    def test_a_different_source_sha_is_a_different_analysis(self, seeded):
        other = source()
        other.inventory = FileInventory("b" * 40, other.inventory.paths, complete=True)
        assert run_analysis(other).analysis_id != run_analysis().analysis_id

    def test_top_bounds_the_list_and_counts_the_rest(self, seeded):
        view = an.summary_view(run_analysis(), top=1)
        assert view["totals"]["shown"] == 1
        assert view["totals"]["more"] == 0


class TestReadOnly:
    def test_nothing_is_written(self, seeded):
        run_analysis()
        with psycopg.connect(TEST_DB) as conn:
            assert conn.execute("SELECT count(*) FROM finding WHERE repo_id = %s",
                                (REPO.id,)).fetchone()[0] == 0

    def test_the_transaction_refuses_writes(self, seeded):
        with psycopg.connect(TEST_DB, row_factory=dict_row) as conn:
            an.analyze(conn, source(), window_days=30)
            with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
                conn.execute("UPDATE repo SET name = name WHERE id = %s", (REPO.id,))


class TestSuppression:
    def test_a_repo_declared_suppression_hides_the_finding_by_default(self, seeded):
        a = run_analysis(source(ignore="long_tail_step — it is the test suite\n"))
        view = an.summary_view(a)
        assert view["findings"] == []
        assert view["totals"]["suppressed_hidden"] == 1
        shown = an.summary_view(a, include_suppressed=True)["findings"][0]
        assert shown["suppressed"]["reason"] == "it is the test suite"

    def test_a_malformed_policy_is_reported_and_suppresses_nothing(self, seeded):
        view = an.summary_view(run_analysis(source(ignore="not a rule\n")))
        assert len(view["findings"]) == 1
        assert any("suppression policy ignored" in w for w in view["warnings"])


class TestFindingView:
    def test_full_evidence_with_the_source_it_points_into(self, seeded):
        a = run_analysis()
        fid = an.summary_view(a)["findings"][0]["id"]
        view = an.finding_view(a, fid)
        f = view["finding"]
        assert f["suggested_action"]
        assert f["detector_version"].startswith("long_tail_step@")
        timing = f["evidence"][0]
        assert timing["payload"]["p50_seconds"] == 600.0
        assert view["run_url"] == "https://github.com/acme/contract/actions/runs/{run_id}"
        assert view["analysis_id"] == a.analysis_id

    def test_an_unknown_id_is_a_coded_refusal(self, seeded):
        with pytest.raises(an.AnalysisError) as exc:
            an.finding_view(run_analysis(), "f_nope")
        assert exc.value.code == "finding_not_found"


class TestRefusals:
    def test_a_repo_with_no_history(self, schema):
        src = PreviewSource("acme/never-ingested",
                            FileInventory(SHA, frozenset({CI}), complete=True), {CI: YAML})
        with pytest.raises(an.AnalysisError) as exc:
            run_analysis(src)
        assert exc.value.code == "not_ingested"
        assert exc.value.as_dict()["error"]["hint"] == "cadence ingest acme/never-ingested"


def test_long_lists_are_bounded_with_their_true_length():
    assert an._bounded(list(range(120))) == {"first": list(range(50)), "total": 120}
    assert an._bounded({"x": [1.234567]}) == {"x": [1.23]}


class TestCli:
    def _invoke(self, monkeypatch, tmp_path, *args):
        from contextlib import contextmanager

        from cadence import cli

        @contextmanager
        def test_connect(*_a, **_k):
            with psycopg.connect(TEST_DB, row_factory=dict_row) as conn:
                yield conn

        monkeypatch.setattr(cli, "connect", test_connect)
        snap = source().save(tmp_path)
        return CliRunner().invoke(cli.app, ["analyze", "--snapshot", str(snap),
                                            "--window", "30", *args])

    def test_stdout_is_exactly_one_json_object(self, seeded, monkeypatch, tmp_path):
        result = self._invoke(monkeypatch, tmp_path)
        assert result.exit_code == 0, result.output
        view = json.loads(result.stdout)
        assert view["findings"][0]["kind"] == "long_tail_step"

    def test_a_finding_by_id_offline(self, seeded, monkeypatch, tmp_path):
        fid = json.loads(self._invoke(monkeypatch, tmp_path).stdout)["findings"][0]["id"]
        result = self._invoke(monkeypatch, tmp_path, "--finding", fid)
        assert json.loads(result.stdout)["finding"]["id"] == fid

    def test_errors_are_json_with_a_code_and_exit_1(self, seeded, monkeypatch, tmp_path):
        result = self._invoke(monkeypatch, tmp_path, "--finding", "f_nope")
        assert result.exit_code == 1
        assert json.loads(result.stdout)["error"]["code"] == "finding_not_found"
