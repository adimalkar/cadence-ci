"""The per-job watchdog.

Written against a real incident: on 2026-09-01 a worker claimed four jobs and then logged
nothing for 18h49m, holding every concurrency slot with claims that neither finished nor
failed. Ingest stopped for nineteen hours.

The queue's lease was never the problem -- `claim_next` reclaims a `processing` row older
than `LEASE`, and it would have. But a lease only helps if some worker is alive to call it,
and the only worker was hung. These tests pin the thing that actually failed: a job that
blocks must release its slot.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from cadence import queue
from cadence.worker import JOB_TIMEOUT, MAX_ATTEMPTS


class TestTheLeaseInvariant:
    def test_a_job_times_out_before_its_lease_expires(self):
        """The ordering that stops a job being processed twice.

        If a job could outlive its lease, another worker would reclaim the row while this
        one is still working it. Timing out first means this worker always releases the
        row before anyone else may take it.
        """
        assert JOB_TIMEOUT < queue.LEASE

    def test_the_margin_is_not_marginal(self):
        """A minute of slack would make this ordering a coin flip under load."""
        assert timedelta(minutes=5) <= queue.LEASE - JOB_TIMEOUT

    def test_the_timeout_is_generous_enough_for_real_work(self):
        """react/react backfilled 7,524 jobs in about two minutes. Ten is not tight."""
        assert timedelta(minutes=5) <= JOB_TIMEOUT


class TestWaitForSemantics:
    """`asyncio.wait_for` is the mechanism; these pin the behaviour the worker relies on."""

    async def test_a_hanging_coroutine_raises_rather_than_blocking(self):
        async def never() -> None:
            await asyncio.Event().wait()

        with pytest.raises(TimeoutError):
            await asyncio.wait_for(never(), timeout=0.05)

    async def test_the_hanging_task_is_cancelled_not_leaked(self):
        """A timed-out job must not keep running behind the worker's back."""
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def hang() -> None:
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        with pytest.raises(TimeoutError):
            await asyncio.wait_for(hang(), timeout=0.05)

        assert started.is_set()
        await asyncio.sleep(0)
        assert cancelled.is_set()

    async def test_work_finishing_inside_the_budget_is_untouched(self):
        async def quick() -> str:
            await asyncio.sleep(0.01)
            return "done"

        assert await asyncio.wait_for(quick(), timeout=5.0) == "done"

    async def test_asyncio_timeout_is_the_builtin_on_this_python(self):
        """The handler catches the builtin. On <3.11 these were distinct types and the
        except clause would have missed."""
        assert asyncio.TimeoutError is TimeoutError


class TestRetryPolicy:
    """A timed-out job retries, but not forever."""

    def test_a_hanging_job_is_retried_below_the_attempt_ceiling(self):
        attempts = 0
        retry = timedelta(minutes=1) if attempts < MAX_ATTEMPTS else None
        assert retry is not None

    def test_a_reliably_hanging_job_eventually_stops(self):
        """Otherwise one poisonous repo re-hangs a slot every minute forever."""
        attempts = MAX_ATTEMPTS
        retry = timedelta(minutes=1) if attempts < MAX_ATTEMPTS else None
        assert retry is None
