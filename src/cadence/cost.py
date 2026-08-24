"""Convert saved seconds into the currency the repo actually pays.

Public repos get standard GitHub-hosted runners for free. Quoting a dollar figure to a
maintainer who knows their CI costs nothing is the fastest way to lose the pitch, so the
headline currency is chosen from the repo, not from what sounds impressive.

Rates are versioned rows in `rate_card`, never constants: GitHub cut hosted prices up to
39% on 2026-01-01, and a shelved self-hosted charge may yet return. Every finding records
the `rate_card_version` that produced its figure so historical claims stay auditable.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import psycopg
from psycopg.rows import dict_row


class Currency(StrEnum):
    HOURS = "hours"
    DOLLARS = "dollars"


@dataclass(slots=True)
class RateCard:
    version: int
    rates: dict[str, float]           # runner label -> usd/minute
    free_on_public: dict[str, bool]

    def usd_per_minute(self, labels: list[str], *, is_private: bool) -> float:
        """Billed rate for a job, or 0.0 when the runner is free for this repo.

        Unknown labels (self-hosted, custom pools) bill at 0.0 rather than guessing --
        a made-up rate would silently fabricate the dollar column.
        """
        for label in labels:
            key = label.strip()
            if key in self.rates:
                if not is_private and self.free_on_public.get(key, False):
                    return 0.0
                return self.rates[key]
        return 0.0


def load_rate_card(conn: psycopg.Connection, version: int) -> RateCard:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT runner_label, usd_per_minute, free_on_public FROM rate_card"
            " WHERE version = %s",
            (version,),
        )
        rows = cur.fetchall()
    return RateCard(
        version=version,
        rates={r["runner_label"]: float(r["usd_per_minute"]) for r in rows},
        free_on_public={r["runner_label"]: r["free_on_public"] for r in rows},
    )


@dataclass(slots=True)
class CostContext:
    """Everything needed to denominate a saving for one repo."""

    is_private: bool
    runs_per_month: float
    rate_card: RateCard
    billed_minutes_per_run: float = 0.0
    dominant_labels: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.dominant_labels is None:
            self.dominant_labels = ["ubuntu-latest"]

    @property
    def headline_currency(self) -> Currency:
        """Dollars only when the repo actually pays them.

        A public repo on larger runners *is* billed, so the test is the effective rate,
        not the visibility flag.
        """
        rate = self.rate_card.usd_per_minute(self.dominant_labels, is_private=self.is_private)
        return Currency.DOLLARS if rate > 0 else Currency.HOURS

    def dollars_per_month(
        self, seconds_saved_per_run: float, *, parallel_jobs: float = 1.0
    ) -> float:
        """Billed dollars recovered per month.

        `parallel_jobs` matters: wall-clock saved on the critical path is elapsed time,
        but billing is per-job-minute. Saving 2 minutes of elapsed time by unblocking one
        job bills as 2 job-minutes; cancelling a superseded run saves every concurrently
        running job's minutes at once.
        """
        rate = self.rate_card.usd_per_minute(self.dominant_labels, is_private=self.is_private)
        if rate <= 0:
            return 0.0
        minutes = (seconds_saved_per_run / 60.0) * parallel_jobs
        return minutes * rate * self.runs_per_month

    def hypothetical_dollars_per_month(
        self, seconds_saved_per_run: float, *, parallel_jobs: float = 1.0
    ) -> float:
        """What this would cost if the repo were private.

        The honest way to give an OSS maintainer a money figure: label it explicitly as
        hypothetical rather than presenting a bill they do not receive.
        """
        rate = self.rate_card.rates.get(self.dominant_labels[0], 0.006)
        minutes = (seconds_saved_per_run / 60.0) * parallel_jobs
        return minutes * rate * self.runs_per_month

    def hours_per_month(self, seconds_saved_per_run: float) -> float:
        return (seconds_saved_per_run * self.runs_per_month) / 3600.0


def render_saving(ctx: CostContext, seconds_per_run: float, *, parallel_jobs: float = 1.0) -> str:
    """One-line headline in the repo's own currency."""
    hours = ctx.hours_per_month(seconds_per_run)
    if ctx.headline_currency is Currency.DOLLARS:
        usd = ctx.dollars_per_month(seconds_per_run, parallel_jobs=parallel_jobs)
        return f"{hours:.1f} hrs/month · ${usd:,.0f}/month"
    usd = ctx.hypothetical_dollars_per_month(seconds_per_run, parallel_jobs=parallel_jobs)
    return f"{hours:.1f} hrs/month (would be ${usd:,.0f}/month on a private repo)"
