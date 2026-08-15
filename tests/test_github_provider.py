from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from cadence.models import Job, Run, Step
from cadence.providers.base import AccessDenied, Expired, NotFound, RateLimited
from cadence.providers.github import API_ROOT, GitHubProvider, _parse_matrix

REPO_JSON = {
    "id": 42,
    "name": "widget",
    "owner": {"login": "acme"},
    "private": False,
    "default_branch": "main",
}


@pytest.fixture
def provider():
    return GitHubProvider("test-token")


def _job_json(**overrides):
    base = {
        "id": 900,
        # Real GitHub job objects carry run_id whether they arrive via the REST list-jobs
        # endpoint or a workflow_job webhook payload; the REST path ignores it in favour
        # of the run_id known from the URL, but normalize_event (no URL context) reads it
        # from here.
        "run_id": 100,
        "name": "test (ubuntu-latest, 3.12)",
        "status": "completed",
        "conclusion": "success",
        "labels": ["ubuntu-latest"],
        "created_at": "2026-08-01T10:00:00Z",
        "started_at": "2026-08-01T10:00:30Z",
        "completed_at": "2026-08-01T10:05:30Z",
        "run_attempt": 1,
        "steps": [
            {
                "number": 1,
                "name": "Checkout",
                "status": "completed",
                "conclusion": "success",
                "started_at": "2026-08-01T10:00:30Z",
                "completed_at": "2026-08-01T10:00:40Z",
            },
            {
                "number": 2,
                "name": "npm ci",
                "status": "completed",
                "conclusion": "success",
                "started_at": "2026-08-01T10:00:40Z",
                "completed_at": "2026-08-01T10:05:30Z",
            },
        ],
    }
    base.update(overrides)
    return base


class TestMatrixParsing:
    """Matrix legs only exist inside job names; parsing them out enables the
    non-discriminating-leg rule, which is one of the highest-value waste findings."""

    def test_extracts_positional_args(self):
        name, matrix = _parse_matrix("test (ubuntu-latest, 3.12)")
        assert name == "test"
        assert matrix == {"0": "ubuntu-latest", "1": "3.12"}

    def test_plain_name_has_no_matrix(self):
        assert _parse_matrix("build") == ("build", None)

    def test_empty_parens_are_not_a_matrix(self):
        assert _parse_matrix("build ()") == ("build ()", None)

    def test_nested_parens_left_alone(self):
        # Avoid mangling names that merely contain punctuation.
        name, matrix = _parse_matrix("lint (py) (3.12)")
        assert matrix == {"0": "3.12"}
        assert name == "lint (py)"


class TestFetchRuns:
    @respx.mock
    async def test_parses_runs_and_captures_etag(self, provider):
        respx.get(f"{API_ROOT}/repos/acme/widget").mock(
            return_value=httpx.Response(200, json=REPO_JSON)
        )
        respx.get(f"{API_ROOT}/repos/acme/widget/actions/runs").mock(
            return_value=httpx.Response(
                200,
                headers={"etag": 'W/"abc123"'},
                json={
                    "total_count": 1,
                    "workflow_runs": [
                        {
                            "id": 1001,
                            "workflow_id": 7,
                            "path": ".github/workflows/ci.yml",
                            "name": "CI",
                            "run_number": 55,
                            "run_attempt": 2,
                            "event": "pull_request",
                            "status": "completed",
                            "conclusion": "failure",
                            "head_sha": "deadbeef",
                            "head_branch": "feature",
                            "created_at": "2026-08-01T10:00:00Z",
                            "run_started_at": "2026-08-01T10:00:05Z",
                            "updated_at": "2026-08-01T10:06:00Z",
                            "pull_requests": [{"number": 314}],
                        }
                    ],
                },
            )
        )
        repo = await provider.get_repo("acme", "widget")
        page = await provider.fetch_runs(repo)

        assert page.etag == 'W/"abc123"'
        assert not page.not_modified
        run = page.runs[0]
        assert run.id == 1001
        assert run.run_attempt == 2  # load-bearing for flaky labelling
        assert run.pull_request_number == 314
        assert run.workflow_path == ".github/workflows/ci.yml"

    @respx.mock
    async def test_304_short_circuits(self, provider):
        respx.get(f"{API_ROOT}/repos/acme/widget").mock(
            return_value=httpx.Response(200, json=REPO_JSON)
        )
        respx.get(f"{API_ROOT}/repos/acme/widget/actions/runs").mock(
            return_value=httpx.Response(304)
        )
        repo = await provider.get_repo("acme", "widget")
        page = await provider.fetch_runs(repo, etag='W/"abc123"')

        assert page.not_modified
        assert page.runs == []
        # The caller must be able to keep its stored etag on a 304.
        assert page.etag == 'W/"abc123"'


class TestFetchJobs:
    @respx.mock
    async def test_parses_steps_and_queue_time(self, provider):
        respx.get(f"{API_ROOT}/repos/acme/widget").mock(
            return_value=httpx.Response(200, json=REPO_JSON)
        )
        respx.get(f"{API_ROOT}/repos/acme/widget/actions/runs/1001/jobs").mock(
            return_value=httpx.Response(200, json={"total_count": 1, "jobs": [_job_json()]})
        )
        repo = await provider.get_repo("acme", "widget")
        jobs = await provider.fetch_jobs(repo, 1001)

        assert len(jobs) == 1
        job = jobs[0]
        # Verbatim name is preserved; the stripped form lives alongside it. Collapsing
        # the two merged unrelated jobs (e.g. "deploy (staging)" and "deploy (production)")
        # into one identity.
        assert job.name == "test (ubuntu-latest, 3.12)"
        assert job.name_base == "test"
        assert job.matrix == {"0": "ubuntu-latest", "1": "3.12"}
        assert len(job.steps) == 2
        assert job.queue_time == timedelta(seconds=30)
        assert job.execution_time == timedelta(minutes=5)
        assert job.steps[1].duration == timedelta(seconds=290)

    @respx.mock
    async def test_paginates(self, provider):
        respx.get(f"{API_ROOT}/repos/acme/widget").mock(
            return_value=httpx.Response(200, json=REPO_JSON)
        )
        route = respx.get(f"{API_ROOT}/repos/acme/widget/actions/runs/1001/jobs")
        route.side_effect = [
            httpx.Response(
                200,
                json={"total_count": 2, "jobs": [_job_json(id=900, name="a")]},
            ),
            httpx.Response(
                200,
                json={"total_count": 2, "jobs": [_job_json(id=901, name="b")]},
            ),
        ]
        repo = await provider.get_repo("acme", "widget")
        jobs = await provider.fetch_jobs(repo, 1001)
        assert [j.id for j in jobs] == [900, 901]


class TestRateLimiting:
    """Getting backoff wrong is how an ingest account gets throttled for hours. These
    assert we read GitHub's own signalling rather than guessing."""

    @respx.mock
    async def test_retry_after_header_is_honoured(self, provider):
        respx.get(f"{API_ROOT}/repos/acme/widget").mock(
            return_value=httpx.Response(429, headers={"retry-after": "120"})
        )
        with pytest.raises(RateLimited) as exc:
            await provider.get_repo("acme", "widget")
        assert exc.value.retry_after_seconds == 120

    @respx.mock
    async def test_primary_limit_uses_reset_epoch(self, provider):
        reset = datetime.now(UTC).timestamp() + 300
        respx.get(f"{API_ROOT}/repos/acme/widget").mock(
            return_value=httpx.Response(
                403,
                headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(reset))},
            )
        )
        with pytest.raises(RateLimited) as exc:
            await provider.get_repo("acme", "widget")
        assert 290 < exc.value.retry_after_seconds <= 300

    @respx.mock
    async def test_404_is_not_found_not_rate_limit(self, provider):
        respx.get(f"{API_ROOT}/repos/acme/ghost").mock(return_value=httpx.Response(404))
        with pytest.raises(NotFound):
            await provider.get_repo("acme", "ghost")

    @respx.mock
    async def test_403_without_rate_limit_signals_is_access_denied(self, provider):
        """GitHub 403s for missing scopes, SAML enforcement, and blocked repos as well
        as for rate limits. Treating all of them as rate limits made a permanently
        forbidden repo retry every 60 seconds forever."""
        respx.get(f"{API_ROOT}/repos/acme/private").mock(
            return_value=httpx.Response(
                403,
                headers={"x-ratelimit-remaining": "4980"},
                json={"message": "Resource not accessible by personal access token"},
            )
        )
        with pytest.raises(AccessDenied):
            await provider.get_repo("acme", "private")

    @respx.mock
    async def test_403_with_exhausted_quota_is_still_rate_limited(self, provider):
        respx.get(f"{API_ROOT}/repos/acme/widget").mock(
            return_value=httpx.Response(
                403,
                headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "99999999999"},
                json={"message": "API rate limit exceeded"},
            )
        )
        with pytest.raises(RateLimited):
            await provider.get_repo("acme", "widget")

    @respx.mock
    async def test_403_secondary_limit_detected_from_body(self, provider):
        respx.get(f"{API_ROOT}/repos/acme/widget").mock(
            return_value=httpx.Response(
                403, json={"message": "You have exceeded a secondary rate limit"}
            )
        )
        with pytest.raises(RateLimited):
            await provider.get_repo("acme", "widget")


class TestFetchLogs:
    @respx.mock
    async def test_expired_logs_return_none_not_an_error(self, provider):
        """410 means the log aged out of GitHub's 90-day window. It can never succeed,
        so it must not raise -- previously it reached raise_for_status() and the job
        burned its whole retry budget before landing as `failed`."""
        respx.get(f"{API_ROOT}/repos/acme/widget").mock(
            return_value=httpx.Response(200, json=REPO_JSON)
        )
        respx.get(f"{API_ROOT}/repos/acme/widget/actions/jobs/900/logs").mock(
            return_value=httpx.Response(410)
        )
        repo = await provider.get_repo("acme", "widget")
        assert await provider.fetch_logs(repo, 900) is None

    @respx.mock
    async def test_missing_logs_return_none(self, provider):
        respx.get(f"{API_ROOT}/repos/acme/widget").mock(
            return_value=httpx.Response(200, json=REPO_JSON)
        )
        respx.get(f"{API_ROOT}/repos/acme/widget/actions/jobs/900/logs").mock(
            return_value=httpx.Response(404)
        )
        repo = await provider.get_repo("acme", "widget")
        assert await provider.fetch_logs(repo, 900) is None

    @respx.mock
    async def test_410_on_a_non_log_endpoint_raises_expired(self, provider):
        respx.get(f"{API_ROOT}/repos/acme/gone").mock(return_value=httpx.Response(410))
        with pytest.raises(Expired):
            await provider.get_repo("acme", "gone")


class TestBillableVsWallClock:
    """Conflating billable time with wall clock is the most common error in CI cost
    analysis: parallel jobs bill concurrently, so billable routinely exceeds wall clock.
    Getting this backwards makes every savings claim wrong by the parallelism factor."""

    def test_parallel_jobs_bill_more_than_wall_clock(self):
        t0 = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)
        jobs = [
            Job(
                id=i,
                run_id=1,
                name=f"job{i}",
                name_base=f"job{i}",
                status="completed",
                conclusion="success",
                runner_labels=["ubuntu-latest"],
                runner_group=None,
                created_at=t0,
                started_at=t0,
                completed_at=t0 + timedelta(minutes=10),
                attempt=1,
                steps=[],
            )
            for i in range(3)
        ]
        run = Run(
            id=1,
            repo_id=42,
            workflow_id=None,
            workflow_path=None,
            workflow_name=None,
            run_number=1,
            run_attempt=1,
            event="push",
            status="completed",
            conclusion="success",
            head_sha="abc",
            head_branch="main",
            created_at=t0,
            started_at=t0,
            updated_at=t0,
            jobs=jobs,
        )
        assert run.wall_clock == timedelta(minutes=10)
        assert run.billable_seconds == 1800  # 3 jobs x 10 min, billed concurrently

    def test_wall_clock_spans_queue_time(self):
        t0 = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)
        job = Job(
            id=1,
            run_id=1,
            name="slow-to-start",
            name_base="slow-to-start",
            status="completed",
            conclusion="success",
            runner_labels=["ubuntu-latest"],
            runner_group=None,
            created_at=t0,
            started_at=t0 + timedelta(minutes=5),  # 5 min queued
            completed_at=t0 + timedelta(minutes=8),
            attempt=1,
            steps=[],
        )
        run = Run(
            id=1,
            repo_id=42,
            workflow_id=None,
            workflow_path=None,
            workflow_name=None,
            run_number=1,
            run_attempt=1,
            event="push",
            status="completed",
            conclusion="success",
            head_sha="abc",
            head_branch="main",
            created_at=t0,
            started_at=t0,
            updated_at=t0,
            jobs=[job],
        )
        # Wall clock includes the queue wait; billable does not. A queue-bound repo gets
        # the opposite advice from a compute-bound one, so these must stay distinct.
        assert run.wall_clock == timedelta(minutes=8)
        assert run.billable_seconds == 180
        assert job.queue_time == timedelta(minutes=5)


class TestStepTimingFidelity:
    """Phase 0 ship criterion: step timings must reconstruct job wall-clock within 2%.
    If they don't, the simulator is built on sand and every dollar figure is wrong."""

    def test_steps_sum_to_job_duration(self):
        t0 = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)
        steps = [
            Step(1, "Checkout", "completed", "success", t0, t0 + timedelta(seconds=10)),
            Step(
                2,
                "npm ci",
                "completed",
                "success",
                t0 + timedelta(seconds=10),
                t0 + timedelta(seconds=300),
            ),
        ]
        job_duration = timedelta(seconds=300)
        step_total = sum((s.duration for s in steps), timedelta())
        error = abs(step_total - job_duration) / job_duration
        assert error < 0.02

    def test_incomplete_step_yields_no_duration(self):
        # A step still running has no duration; callers must not treat that as zero.
        step = Step(1, "running", "in_progress", None, datetime.now(UTC), None)
        assert step.duration is None


class TestProvisioningOverhead:
    """A job is not just its steps. Measured against real history (astral-sh/ruff,
    prettier/prettier) it decomposes as:

        job.started_at ──[provisioning]── step1..stepN ──[cleanup]── job.completed_at

    Provisioning is runner allocation: real time, billed and felt, but not step time and
    not fixable by editing steps. On ruff it is 9.5% of job wall-clock and ~2.2 min/run
    across 13.6 jobs/run; on prettier, with 3.1 jobs/run, it is ~0.

    That gap is a rule: parallelism carries a fixed per-job tax, so "parallelize more" --
    the universal CI advice -- has a floor. Conflating provisioning with step time hides
    it, which is why the two are modelled separately from the start.
    """

    @staticmethod
    def _job_with_lead(lead_s: int, step_s: int = 100) -> Job:
        t0 = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)
        first_step_start = t0 + timedelta(seconds=lead_s)
        return Job(
            id=1,
            run_id=1,
            name="test",
            name_base="test",
            status="completed",
            conclusion="success",
            runner_labels=["ubuntu-latest"],
            runner_group=None,
            created_at=t0,
            started_at=t0,
            completed_at=first_step_start + timedelta(seconds=step_s + 2),  # +2s cleanup
            attempt=1,
            steps=[
                Step(
                    1,
                    "Run tests",
                    "completed",
                    "success",
                    first_step_start,
                    first_step_start + timedelta(seconds=step_s),
                )
            ],
        )

    def test_step_sum_is_less_than_job_duration(self):
        job = self._job_with_lead(lead_s=23, step_s=100)
        step_total = sum((s.duration for s in job.steps), timedelta())
        assert step_total == timedelta(seconds=100)
        # 23s provisioning + 100s steps + 2s cleanup
        assert job.execution_time == timedelta(seconds=125)
        # Comparing step sum to job duration measures overhead, NOT ingest fidelity --
        # this is the distinction that made the first fidelity metric read as a failure.
        assert step_total < job.execution_time

    def test_steps_tile_their_own_span(self):
        """The real ship criterion: steps must tile the span they cover. Verified at
        0.03% mean error over 1,401 real ruff jobs and 0.09% over 266 prettier jobs."""
        job = self._job_with_lead(lead_s=23, step_s=100)
        span = job.steps[-1].completed_at - job.steps[0].started_at
        step_total = sum((s.duration for s in job.steps), timedelta())
        assert abs(step_total - span) / span < 0.02

    def test_provisioning_scales_with_job_count(self):
        """The finding in one assertion: a wide matrix pays the tax N times."""
        lead = 10
        wide = [self._job_with_lead(lead) for _ in range(14)]
        narrow = [self._job_with_lead(lead) for _ in range(3)]
        wide_overhead = lead * len(wide)
        narrow_overhead = lead * len(narrow)
        assert wide_overhead - narrow_overhead == 110  # ~2 min/run difference


class TestNormalizeEvent:
    """Webhook ingest and REST polling must never silently diverge in how they parse a
    run or job -- normalize_event reuses the same _to_run/_to_job static methods the
    polling path calls, and these tests pin that down."""

    @pytest.fixture
    def provider(self):
        return GitHubProvider("test-token")

    def test_workflow_run_event_extracts_run(self, provider):
        payload = {
            "action": "completed",
            "repository": {
                "id": 42, "owner": {"login": "acme"}, "name": "widget", "private": False
            },
            "workflow_run": {
                "id": 555,
                "workflow_id": 7,
                "path": ".github/workflows/ci.yml",
                "name": "CI",
                "run_number": 1,
                "run_attempt": 2,
                "event": "push",
                "status": "completed",
                "conclusion": "success",
                "head_sha": "abc",
                "head_branch": "main",
                "created_at": "2026-08-01T10:00:00Z",
                "run_started_at": "2026-08-01T10:00:01Z",
                "updated_at": "2026-08-01T10:05:00Z",
                "pull_requests": [],
            },
        }
        event = provider.normalize_event("workflow_run", payload)
        assert event is not None
        assert event.repo.id == 42
        assert event.repo.full_name == "acme/widget"
        assert event.run.id == 555
        assert event.run.run_attempt == 2  # same field the polling path treats as load-bearing
        assert event.jobs == []

    def test_workflow_job_event_extracts_job_with_steps_and_matrix(self, provider):
        payload = {
            "action": "completed",
            "repository": {
                "id": 42, "owner": {"login": "acme"}, "name": "widget", "private": False
            },
            "workflow_job": _job_json(
                name="test (ubuntu-latest, 3.12)",
                workflow_name="CI", head_sha="deadbeef", head_branch="main",
            ),
        }
        event = provider.normalize_event("workflow_job", payload)
        assert event is not None
        assert len(event.jobs) == 1
        job = event.jobs[0]
        # Same matrix-parsing behaviour as the REST path -- not reimplemented here.
        assert job.name == "test (ubuntu-latest, 3.12)"
        assert job.name_base == "test"
        assert job.matrix == {"0": "ubuntu-latest", "1": "3.12"}
        assert len(job.steps) == 2

    def test_workflow_job_event_also_yields_a_stub_run(self, provider):
        """job.run_id has a foreign key to run, and a workflow_job event can arrive with
        no workflow_run row recorded yet -- the stub is what makes that insert
        satisfiable. Its status/conclusion must stay unset: a job's outcome is not the
        run's outcome, and claiming otherwise would be a wrong, confident answer."""
        payload = {
            "action": "completed",
            "repository": {
                "id": 42, "owner": {"login": "acme"}, "name": "widget", "private": False
            },
            "workflow_job": _job_json(
                run_id=555, workflow_name="CI", head_sha="deadbeef", head_branch="main",
            ),
        }
        event = provider.normalize_event("workflow_job", payload)
        assert event.run is not None
        assert event.run.id == 555
        assert event.run.head_sha == "deadbeef"
        assert event.run.head_branch == "main"
        assert event.run.workflow_name == "CI"
        assert event.run.status is None
        assert event.run.conclusion is None

    def test_missing_repository_returns_none(self, provider):
        assert provider.normalize_event("workflow_run", {"workflow_run": {}}) is None

    def test_missing_workflow_run_object_returns_none(self, provider):
        payload = {"repository": {"id": 1, "owner": {"login": "a"}, "name": "b", "private": False}}
        assert provider.normalize_event("workflow_run", payload) is None

    def test_unknown_event_type_returns_none(self, provider):
        payload = {"repository": {"id": 1, "owner": {"login": "a"}, "name": "b", "private": False}}
        assert provider.normalize_event("issue_comment", payload) is None
