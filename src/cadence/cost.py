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

        An unknown label means a self-hosted or third-party pool, and **GitHub charges
        nothing for those**. The platform charge that card 20260301 priced at $0.002/min
        was announced for private-repo self-hosted runners and postponed indefinitely
        within 48 hours; it never took effect (see migration `008`). Card 20260901 prices
        the sentinel at 0.0, which is the true GitHub charge.

        Cards are never rewritten, only superseded: an old `rate_card_version` still
        resolves through its own row, so a figure published under 20260301 still
        reproduces — wrongly, but reproducibly, which is what makes it auditable.

        **0.0 is what GitHub bills, not what the runner costs.** An EC2 instance or a rack
        is real money; that number belongs to the operator, so `CostContext` takes an
        optional `self_hosted_usd_per_minute` override rather than this card inventing one.
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
    # What a self-hosted minute costs the operator, if they told us. GitHub bills nothing
    # for self-hosted runners, so every CI cost tool reports $0 for them -- while the EC2
    # instance or the rack is real money nobody attributes. We cannot know that number and
    # will not invent one, but an operator who supplies it gets their fleet denominated in
    # the same dollars as everything else. `None` means "unknown", which stays $0 rather
    # than becoming a guess.
    self_hosted_usd_per_minute: float | None = None

    def __post_init__(self) -> None:
        if self.dominant_labels is None:
            self.dominant_labels = ["ubuntu-latest"]
        if self.self_hosted_usd_per_minute is not None and self.self_hosted_usd_per_minute < 0:
            raise ValueError("self_hosted_usd_per_minute cannot be negative")

    def effective_rate(self) -> float:
        """Dollars per minute for this repo's dominant runner.

        The operator's self-hosted rate applies only when the card resolves to 0 *and* the
        runner is unrecognised -- i.e. when GitHub is charging nothing and we are pricing
        the operator's own hardware. It never overrides a known hosted rate, because
        GitHub's bill for those is not the operator's to restate.
        """
        rate = self.rate_card.usd_per_minute(
            self.dominant_labels, is_private=self.is_private
        )
        if rate > 0 or self.self_hosted_usd_per_minute is None:
            return rate
        known = any(label.strip() in self.rate_card.rates for label in self.dominant_labels)
        return 0.0 if known else self.self_hosted_usd_per_minute

    @property
    def headline_currency(self) -> Currency:
        """Dollars only when the repo actually pays them.

        A public repo on larger runners *is* billed, so the test is the effective rate,
        not the visibility flag. An operator-supplied self-hosted rate counts too: if they
        told us their fleet costs money, dollars are the currency they think in.
        """
        return Currency.DOLLARS if self.effective_rate() > 0 else Currency.HOURS

    def dollars_per_month(
        self, seconds_saved_per_run: float, *, parallel_jobs: float = 1.0
    ) -> float:
        """Billed dollars recovered per month.

        `parallel_jobs` matters: wall-clock saved on the critical path is elapsed time,
        but billing is per-job-minute. Saving 2 minutes of elapsed time by unblocking one
        job bills as 2 job-minutes; cancelling a superseded run saves every concurrently
        running job's minutes at once.
        """
        rate = self.effective_rate()
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
