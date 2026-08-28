"""Convert saved seconds into the currency the repo actually pays.

Public repos get standard GitHub-hosted runners for free. Quoting a dollar figure to a
maintainer who knows their CI costs nothing is the fastest way to lose the pitch, so the
headline currency is chosen from the repo, not from what sounds impressive.

Rates are versioned rows in `rate_card`, never constants: GitHub cut hosted prices up to
39% on 2026-01-01, and on 2026-03-01 extended a $0.002/min platform charge to self-hosted
runners as well. Every finding records the `rate_card_version` that produced its figure so
historical claims stay auditable.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import psycopg
from psycopg.rows import dict_row


class Currency(StrEnum):
    HOURS = "hours"
    DOLLARS = "dollars"


# Sentinel label carrying the rate for any runner the card does not name. Self-hosted and
# third-party pools cannot be enumerated -- the label is whatever the repo chose.
SELF_HOSTED = "__self_hosted__"


@dataclass(slots=True)
class RateCard:
    version: int
    rates: dict[str, float]           # runner label -> usd/minute
    free_on_public: dict[str, bool]

    def usd_per_minute(self, labels: list[str], *, is_private: bool) -> float:
        """Billed rate for a job, or 0.0 when the runner is free for this repo.

        An unknown label means a self-hosted or third-party pool. Until 2026-03-01 those
        were genuinely free and billing them at 0.0 was correct; since then GitHub applies
        its platform charge to them too, so they resolve to the card's SELF_HOSTED rate.
        Cards predating that row keep the old behaviour and return 0.0, which is what makes
        an old `rate_card_version` still reproduce the figure it originally published.
        """
        for label in labels:
            key = label.strip()
            if key in self.rates:
                return self._rate(key, is_private=is_private)
        if SELF_HOSTED in self.rates:
            return self._rate(SELF_HOSTED, is_private=is_private)
        return 0.0

    def _rate(self, key: str, *, is_private: bool) -> float:
        if not is_private and self.free_on_public.get(key, False):
            return 0.0
        return self.rates[key]


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

        Resolved through the same path as the real bill, with is_private forced true. It
        previously fell back to a hard-coded 0.006 -- the hosted-Linux rate -- for unknown
        labels, while usd_per_minute returned 0.0 for the very same runner. One runner, two
        prices, and both could appear in one report.
        """
        rate = self.rate_card.usd_per_minute(self.dominant_labels, is_private=True)
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
