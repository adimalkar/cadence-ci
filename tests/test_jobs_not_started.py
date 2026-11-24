"""Jobs that failed without running a step: GitHub refused them (CAVEATS 88).

Synthetic fixtures. `BILLING` is GitHub's documented refusal message for hosted runners.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from cadence.audit import enrich_refusal_reasons
from cadence.cost import CostContext, RateCard
from cadence.detectors.context import AuditContext, UnstartedFailure
from cadence.detectors.unstarted import (
    DETECTOR_VERSION,
    JobsNotStartedDetector,
    refusal_groups,
)

BILLING = ("The job was not started because recent account payments have failed or your "
           "spending limit needs to be increased. Please check the 'Billing & plans' section "
           "in your settings")
T0 = datetime(2026, 9, 21, 9, tzinfo=UTC).timestamp()


def refused(n: int, *, labels=("ubuntu-latest",), start: int = 0, hours: float = 1.0):
    return [UnstartedFailure(job_id=1000 + start + i, run_id=start + i, attempt=1,
                             workflow_path=".github/workflows/ci.yml", job_name="build",
                             runner_labels=tuple(labels), created_epoch=T0 + i * hours * 3600)
            for i in range(n)]


def ctx(failures, annotations=None) -> AuditContext:
    return AuditContext(
        repo_id=1, owner="acme", name="widget", is_private=True, workflows=[], runs=[],
        step_series={},
        cost=CostContext(is_private=True, runs_per_month=100.0,
                         rate_card=RateCard(version=2026, rates={}, free_on_public={})),
        window_days=30, unstarted_failures=failures, job_annotations=annotations or {},
    )


class TestFinding:
    def test_refused_hosted_jobs_are_reported_with_githubs_reason(self):
        failures = refused(6)
        finding, = JobsNotStartedDetector().run(ctx(failures, {1005: [BILLING]}))
        assert finding.title == (
            "6 jobs on hosted runners (`ubuntu-latest`) failed without running a step, "
            "2026-09-21: GitHub says \"The job was not started because recent account "
            "payments have failed or your spending limit needs to be increased\"")
        assert finding.detector_version == DETECTOR_VERSION
        assert finding.module == "observability"
        assert (finding.severity, finding.confidence) == (3, 0.95)
        assert finding.dedupe_key == "jobs_not_started:hosted:ubuntu-latest"
        assert finding.savings is None
        evidence, = finding.evidence
        assert evidence.run_ids == list(range(6))
        assert evidence.payload["reasons"] == [BILLING]
        assert evidence.payload["reason_looked_up"] is True
        assert "Billing & plans" in finding.suggested_action

    def test_many_refusals_raise_severity_and_spans_name_both_days(self):
        finding, = JobsNotStartedDetector().run(ctx(refused(50)))
        assert finding.severity == 4
        assert "2026-09-21 to 2026-09-23" in finding.title

    def test_an_unknown_reason_is_said_to_be_unknown(self):
        finding, = JobsNotStartedDetector().run(ctx(refused(5)))
        assert "GitHub says" not in finding.title
        assert finding.confidence == 0.8
        assert "billing block or spending limit" in finding.suggested_action
        assert finding.evidence[0].payload["reason_looked_up"] is False

    def test_self_hosted_refusals_point_at_runner_registration(self):
        finding, = JobsNotStartedDetector().run(ctx(refused(5, labels=("self-hosted", "gpu"))))
        assert "self-hosted runners (`gpu`, `self-hosted`)" in finding.title
        assert "registered and online" in finding.suggested_action

    def test_too_few_stay_quiet(self):
        assert JobsNotStartedDetector().run(ctx(refused(4))) == []


class TestGroups:
    def test_grouped_by_where_the_job_asked_to_run_largest_first(self):
        failures = refused(3, labels=("self-hosted", "linux")) + refused(
            7, labels=("ubuntu-latest",), start=100)
        groups = refusal_groups(failures)
        assert [len(g) for g in groups] == [7, 3]
        assert len(JobsNotStartedDetector().run(ctx(failures))) == 1


class FakeProvider:
    def __init__(self, notes, *, fail=False):
        self.notes, self.fail, self.calls = notes, fail, []

    async def fetch_job_annotations(self, repo, job_id):
        self.calls.append(job_id)
        if self.fail:
            raise RuntimeError("boom")
        return self.notes


class TestEnrichment:
    def test_reads_the_newest_job_per_group_and_keeps_failures_only(self):
        c = ctx(refused(6))
        provider = FakeProvider([("failure", BILLING), ("notice", "label migrating")])
        assert asyncio.run(enrich_refusal_reasons(provider, None, c)) == 1
        assert provider.calls == [1005]
        assert c.job_annotations == {1005: [BILLING]}

    def test_lookups_are_capped(self):
        failures = [f for i in range(5)
                    for f in refused(1, labels=(f"label-{i}",), start=10 * i)]
        provider = FakeProvider([])
        asyncio.run(enrich_refusal_reasons(provider, None, ctx(failures), max_lookups=3))
        assert len(provider.calls) == 3

    def test_a_failed_lookup_leaves_the_reason_unknown(self):
        c = ctx(refused(6))
        assert asyncio.run(enrich_refusal_reasons(FakeProvider([], fail=True), None, c)) == 0
        assert c.job_annotations == {}


TEST_DB = os.environ.get("CADENCE_TEST_DATABASE_URL", "postgresql://localhost/cadence_test")


def _db_available() -> bool:
    try:
        with psycopg.connect(TEST_DB, connect_timeout=2):
            return True
    except Exception:
        return False


@pytest.mark.skipif(not _db_available(), reason="no test database")
def test_build_context_collects_failed_jobs_that_ran_no_step():
    from psycopg.rows import dict_row

    from cadence.audit import build_context
    from cadence.db.conn import apply_migrations
    from cadence.ingest import upsert_job, upsert_repo, upsert_run
    from cadence.models import Job, Repo, Run, Step

    apply_migrations(TEST_DB)
    repo = Repo(id=8181, owner="acme", name="refused", is_private=True, default_branch="main")
    t0 = datetime.now(UTC) - timedelta(hours=2)
    with psycopg.connect(TEST_DB, row_factory=dict_row) as conn:
        try:
            upsert_repo(conn, repo)
            upsert_run(conn, Run(
                id=81, repo_id=repo.id, workflow_id=1, workflow_path=".github/workflows/ci.yml",
                workflow_name="CI", run_number=1, run_attempt=1, event="push",
                status="completed", conclusion="failure", head_sha="abc", head_branch="main",
                created_at=t0, started_at=t0, updated_at=t0 + timedelta(minutes=5),
            ))

            def job(job_id, steps):
                return Job(id=job_id, run_id=81, name=f"j{job_id}", name_base=f"j{job_id}",
                           status="completed", conclusion="failure",
                           runner_labels=["ubuntu-latest"], runner_group=None, created_at=t0,
                           started_at=t0, completed_at=t0 + timedelta(seconds=3), attempt=1,
                           steps=steps)

            upsert_job(conn, job(811, []), repo.id)
            upsert_job(conn, job(812, [Step(1, "Test", "completed", "failure", t0,
                                            t0 + timedelta(seconds=2))]), repo.id)
            # A reusable-workflow caller: no labels, no steps, failed with its callee.
            caller = job(813, [])
            caller.runner_labels = []
            upsert_job(conn, caller, repo.id)
            # Ran for ten minutes and recorded no steps: lost them, was not refused.
            long = job(814, [])
            long.completed_at = t0 + timedelta(minutes=10)
            upsert_job(conn, long, repo.id)
            conn.commit()
            built = build_context(conn, repo.id, {}, window_days=1)
            u, = built.unstarted_failures
            assert (u.job_id, u.run_id, u.runner_labels) == (811, 81, ("ubuntu-latest",))

            # Past the step-retention horizon nothing has steps, so absence proves nothing.
            conn.execute("DELETE FROM step WHERE job_id = 812")
            conn.commit()
            assert build_context(conn, repo.id, {}, window_days=1).unstarted_failures == []
        finally:
            conn.rollback()
            conn.execute("DELETE FROM step WHERE job_id IN (811, 812, 813, 814)")
            conn.execute("DELETE FROM job WHERE repo_id = %s", (repo.id,))
            conn.execute("DELETE FROM run WHERE repo_id = %s", (repo.id,))
            conn.execute("DELETE FROM repo WHERE id = %s", (repo.id,))
            conn.commit()
