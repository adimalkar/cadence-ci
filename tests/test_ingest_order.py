"""Real SQL replay: delivery order must not erase a valid terminal observation."""

import itertools
import os
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg.rows import dict_row

from cadence import config
from cadence.db import apply_migrations
from cadence.ingest import ensure_run_stub, ingest_repo, upsert_job, upsert_repo, upsert_run
from cadence.models import Job, Repo, Run, RunPage, Step
from cadence.providers.github import GitHubProvider
from cadence.worker import _handle_webhook_event

TEST_DB = os.environ.get("CADENCE_TEST_DATABASE_URL", "postgresql://localhost/cadence_test")
REPO = Repo(8_950_001, "lifecycle-test", "rollback", False, "main")
RUN_ID = 8_950_100
JOB_ID = 8_950_900
T0 = datetime(2026, 11, 18, tzinfo=UTC)
PHASES = ("queued", "in_progress", "completed")


def run(phase="completed", **overrides):
    rank = PHASES.index(phase)
    return replace(
        Run(
            RUN_ID,
            REPO.id,
            7,
            "ci.yml",
            "CI",
            1,
            1,
            "push",
            phase,
            "success" if rank == 2 else None,
            "a" * 40,
            "main",
            T0,
            T0 + timedelta(seconds=10) if rank else None,
            T0 + timedelta(seconds=rank * 50),
        ),
        **overrides,
    )


def job(phase="completed", **overrides):
    rank = PHASES.index(phase)
    start = T0 + timedelta(seconds=10) if rank else None
    end = T0 + timedelta(seconds=100) if rank == 2 else None
    step = Step(1, "build", phase, "success" if rank == 2 else None, start, end)
    return replace(
        Job(
            JOB_ID,
            RUN_ID,
            "build",
            "build",
            phase,
            step.conclusion,
            ["ubuntu-latest"] if rank else [],
            None,
            T0,
            start,
            end,
            1,
            steps=[step],
        ),
        **overrides,
    )


@pytest.fixture(scope="module", autouse=True)
def schema():
    apply_migrations(TEST_DB)


@pytest.fixture
def conn():
    with psycopg.connect(TEST_DB, row_factory=dict_row) as conn:
        upsert_repo(conn, REPO)
        yield conn
        conn.rollback()
        conn.execute("DELETE FROM repo WHERE id=%s", (REPO.id,))
        conn.commit()


@pytest.mark.parametrize("order", list(itertools.permutations(PHASES)))
def test_run_delivery_permutations_converge(conn, order):
    for phase in order:
        upsert_run(conn, run(phase))
    saved = conn.execute("SELECT * FROM run WHERE id=%s", (RUN_ID,)).fetchone()
    assert saved["status"] == "completed" and saved["conclusion"] == "success"
    assert saved["updated_at"] == run().updated_at
    assert saved["started_at"] == run().started_at


@pytest.mark.parametrize("order", list(itertools.permutations(PHASES)))
def test_job_and_step_delivery_permutations_converge(conn, order):
    upsert_run(conn, run())
    for phase in order:
        upsert_job(conn, job(phase), REPO.id)
    saved = conn.execute("SELECT * FROM job WHERE id=%s", (JOB_ID,)).fetchone()
    step = conn.execute("SELECT * FROM step WHERE job_id=%s", (JOB_ID,)).fetchone()
    for row in (saved, step):
        assert row["status"] == "completed" and row["conclusion"] == "success"
        assert row["started_at"] == job().started_at and row["completed_at"] == job().completed_at
    assert saved["runner_labels"] == ["ubuntu-latest"]


def test_late_nonterminal_payload_cannot_replace_known_times_or_runner_metadata(conn):
    upsert_run(conn, run())
    upsert_job(conn, job(runner_group="original", matrix={"os": "linux"}), REPO.id)
    before = conn.execute("SELECT * FROM job WHERE id=%s", (JOB_ID,)).fetchone()
    late = job(
        "in_progress",
        created_at=T0 + timedelta(seconds=20),
        started_at=T0 + timedelta(seconds=30),
        runner_labels=["windows-latest"],
        runner_group="different",
        matrix={"os": "windows"},
    )
    upsert_job(conn, late, REPO.id)
    assert conn.execute("SELECT * FROM job WHERE id=%s", (JOB_ID,)).fetchone() == before


def test_missing_job_and_step_fields_can_still_enrich_after_terminal_state(conn):
    upsert_run(conn, run())
    incomplete = job(
        created_at=None,
        started_at=None,
        completed_at=None,
        runner_labels=[],
        runner_group=None,
        conclusion=None,
    )
    incomplete.steps[0] = replace(
        incomplete.steps[0], started_at=None, completed_at=None, conclusion=None
    )
    upsert_job(conn, incomplete, REPO.id)
    upsert_job(conn, job(runner_group="hosted"), REPO.id)
    saved = conn.execute("SELECT * FROM job WHERE id=%s", (JOB_ID,)).fetchone()
    step = conn.execute("SELECT * FROM step WHERE job_id=%s", (JOB_ID,)).fetchone()
    assert saved["created_at"] == T0 and saved["runner_group"] == "hosted"
    assert saved["conclusion"] == step["conclusion"] == "success"
    assert step["started_at"] == job().started_at


def test_new_attempt_clears_old_start_and_conclusion_then_rejects_old_attempt(conn):
    upsert_run(conn, run())
    assert upsert_run(conn, run("queued", run_attempt=2, updated_at=T0 + timedelta(seconds=200)))
    saved = conn.execute("SELECT * FROM run WHERE id=%s", (RUN_ID,)).fetchone()
    assert saved["run_attempt"] == 2 and saved["conclusion"] is None and saved["started_at"] is None
    assert not upsert_run(conn, run(updated_at=T0 + timedelta(seconds=300)))
    assert conn.execute("SELECT * FROM run WHERE id=%s", (RUN_ID,)).fetchone() == saved


@pytest.mark.parametrize(
    "overrides",
    [
        {"updated_at": T0},
        {"updated_at": None},
        {"run_attempt": 2, "updated_at": None},
        {"run_attempt": 2, "updated_at": T0},
        {"status": "in_progress", "updated_at": T0 + timedelta(seconds=300)},
        {"conclusion": "failure"},
    ],
)
def test_stale_or_ambiguous_run_revision_is_ignored(conn, overrides):
    upsert_run(conn, run())
    saved = conn.execute("SELECT * FROM run WHERE id=%s", (RUN_ID,)).fetchone()
    assert not upsert_run(conn, run(**overrides))
    assert conn.execute("SELECT * FROM run WHERE id=%s", (RUN_ID,)).fetchone() == saved


def test_newer_run_revision_can_correct_conclusion(conn):
    upsert_run(conn, run())
    assert upsert_run(conn, run(conclusion="failure", updated_at=T0 + timedelta(seconds=200)))
    assert (
        conn.execute("SELECT conclusion FROM run WHERE id=%s", (RUN_ID,)).fetchone()["conclusion"]
        == "failure"
    )


def test_full_run_can_replace_job_stub_start(conn):
    ensure_run_stub(conn, run(started_at=T0 + timedelta(seconds=50)))
    assert upsert_run(conn, run())
    assert (
        conn.execute("SELECT started_at FROM run WHERE id=%s", (RUN_ID,)).fetchone()["started_at"]
        == run().started_at
    )


def test_unknown_status_does_not_make_terminal_conflict_null(conn):
    upsert_run(conn, run(status=None, updated_at=None))
    assert upsert_run(conn, run())
    upsert_job(conn, job(status=None, conclusion="success"), REPO.id)
    upsert_job(conn, job(), REPO.id)
    assert (
        conn.execute("SELECT status FROM job WHERE id=%s", (JOB_ID,)).fetchone()["status"]
        == "completed"
    )


@pytest.mark.parametrize(
    "overrides", [{"run_id": RUN_ID + 1}, {"attempt": 2}, {"conclusion": "failure"}]
)
def test_job_conflict_is_rejected_before_touching_steps(conn, overrides):
    upsert_run(conn, run())
    upsert_run(conn, run(id=RUN_ID + 1))
    upsert_job(conn, job(), REPO.id)
    saved = conn.execute("SELECT * FROM job WHERE id=%s", (JOB_ID,)).fetchone()
    with pytest.raises(ValueError, match="conflicts"):
        upsert_job(conn, job(**overrides), REPO.id)
    assert conn.execute("SELECT * FROM job WHERE id=%s", (JOB_ID,)).fetchone() == saved


def test_late_step_conflict_rolls_back_job_enrichment_and_earlier_step_insert(conn):
    upsert_run(conn, run())
    old = job()
    old.steps[0].number = 2
    upsert_job(conn, old, REPO.id)
    new = job(runner_group="enrichment")
    new.steps += [replace(new.steps[0], number=2, conclusion="failure")]
    with pytest.raises(ValueError, match="step terminal"), conn.transaction():
        upsert_job(conn, new, REPO.id)
    assert (
        conn.execute("SELECT runner_group FROM job WHERE id=%s", (JOB_ID,)).fetchone()[
            "runner_group"
        ]
        is None
    )
    assert [
        s["number"] for s in conn.execute("SELECT number FROM step WHERE job_id=%s", (JOB_ID,))
    ] == [2]


def test_cross_repo_run_collision_is_not_a_silent_stale_skip(conn):
    upsert_run(conn, run())
    with pytest.raises(ValueError, match="repository"):
        upsert_run(conn, run(repo_id=REPO.id + 1))


async def test_ingest_counts_ignored_revisions(conn):
    upsert_run(conn, run())
    conn.commit()

    class Provider:
        async def get_repo(self, *args):
            return REPO

        async def fetch_runs(self, *args, **kwargs):
            return RunPage([run("queued")], '"same"')

    stats = await ingest_repo(Provider(), conn, REPO.owner, REPO.name, limit=1, fetch_jobs=False)
    assert stats.runs_seen == 1 and stats.runs_written == 0 and stats.runs_ignored == 1


async def test_actual_worker_replays_completed_then_queued_delivery(conn, monkeypatch):
    upsert_run(conn, run())
    conn.commit()
    monkeypatch.setattr(config.settings, "database_url", TEST_DB)
    provider = GitHubProvider("unused-token")
    try:
        for phase in ("completed", "queued"):
            item = job(phase)
            payload = {
                "repository": {
                    "id": REPO.id,
                    "owner": {"login": REPO.owner},
                    "name": REPO.name,
                    "private": False,
                },
                "workflow_job": {
                    "id": JOB_ID,
                    "run_id": RUN_ID,
                    "run_attempt": 1,
                    "head_sha": "a" * 40,
                    "head_branch": "main",
                    "name": item.name,
                    "status": item.status,
                    "conclusion": item.conclusion,
                    "labels": item.runner_labels,
                    "created_at": T0.isoformat(),
                    "started_at": item.started_at.isoformat() if item.started_at else None,
                    "completed_at": item.completed_at.isoformat() if item.completed_at else None,
                    "steps": [],
                },
            }
            await _handle_webhook_event(
                provider,
                {
                    "payload": {
                        "event": "workflow_job",
                        "delivery_id": str(uuid.uuid4()),
                        "body": payload,
                    }
                },
            )
    finally:
        await provider.aclose()
    saved = conn.execute(
        "SELECT status,conclusion,completed_at FROM job WHERE id=%s", (JOB_ID,)
    ).fetchone()
    assert saved == {
        "status": "completed",
        "conclusion": "success",
        "completed_at": job().completed_at,
    }


@pytest.mark.parametrize("attempt", [0, True, 2**31])
def test_invalid_run_attempt_is_rejected_before_writing(conn, attempt):
    with pytest.raises(ValueError, match="attempt"):
        upsert_run(conn, run(run_attempt=attempt))
    assert conn.execute("SELECT id FROM run WHERE id=%s", (RUN_ID,)).fetchone() is None


def test_job_repository_collision_does_not_change_existing_evidence(conn):
    upsert_run(conn, run())
    upsert_job(conn, job(), REPO.id)
    before = conn.execute("SELECT * FROM job WHERE id=%s", (JOB_ID,)).fetchone()
    with pytest.raises(ValueError, match="conflicts"):
        upsert_job(conn, job(), REPO.id + 1)
    assert conn.execute("SELECT * FROM job WHERE id=%s", (JOB_ID,)).fetchone() == before
