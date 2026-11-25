"""Fixtures shared by the analysis-contract tests and the MCP adapter tests."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
import structlog
from psycopg.rows import dict_row

from cadence.db.conn import apply_migrations
from cadence.ingest import upsert_job, upsert_repo, upsert_run
from cadence.models import Job, Repo, Run, Step
from cadence.source import FileInventory, PreviewSource

TEST_DB = os.environ.get("CADENCE_TEST_DATABASE_URL", "postgresql://localhost/cadence_test")


@pytest.fixture(autouse=True)
def _restore_structlog():
    """A command under test may reroute structlog to stderr; never let that leak."""
    yield
    structlog.reset_defaults()


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


