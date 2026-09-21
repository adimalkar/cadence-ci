"""Rate-card resolution, including the self-hosted charge that took effect 2026-03-01.

The bug these pin: `usd_per_minute` returned 0.0 for any label the card did not name, which
was correct while self-hosted minutes were free and wrong afterwards -- and
`hypothetical_dollars_per_month` independently fell back to 0.006, the hosted-Linux rate,
for the same unknown label. One runner could be priced two ways in one report.
"""

from __future__ import annotations

import pytest

from cadence.cost import SELF_HOSTED, CostContext, Currency, RateCard

HOSTED = "ubuntu-latest"
BIG = "ubuntu-latest-8-core"
SELF = "depot-ubuntu-24.04-4"   # a real label from the corpus; not in any rate card


def card(*, with_self_hosted: bool) -> RateCard:
    rates = {HOSTED: 0.006, BIG: 0.024}
    free = {HOSTED: True, BIG: False}
    if with_self_hosted:
        rates[SELF_HOSTED] = 0.002
        free[SELF_HOSTED] = True
    return RateCard(version=20260301 if with_self_hosted else 2026,
                    rates=rates, free_on_public=free)


class TestKnownLabels:
    def test_private_repo_pays_the_listed_rate(self):
        assert card(with_self_hosted=True).usd_per_minute([HOSTED], is_private=True) == 0.006

    def test_public_repo_pays_nothing_for_a_free_runner(self):
        assert card(with_self_hosted=True).usd_per_minute([HOSTED], is_private=False) == 0.0

    def test_larger_runners_are_billed_even_on_public_repos(self):
        assert card(with_self_hosted=True).usd_per_minute([BIG], is_private=False) == 0.024

    def test_first_matching_label_wins(self):
        assert card(with_self_hosted=True).usd_per_minute(
            [SELF, BIG], is_private=True) == 0.024


class TestSelfHostedFallback:
    def test_unknown_label_bills_the_platform_charge_on_a_private_repo(self):
        # The regression: this used to be 0.0, so every self-hosted private job read as free.
        assert card(with_self_hosted=True).usd_per_minute([SELF], is_private=True) == 0.002

    def test_unknown_label_is_still_free_on_a_public_repo(self):
        assert card(with_self_hosted=True).usd_per_minute([SELF], is_private=False) == 0.0

    def test_label_whitespace_is_tolerated(self):
        assert card(with_self_hosted=True).usd_per_minute(
            [f"  {HOSTED}  "], is_private=True) == 0.006

    def test_no_labels_at_all_falls_through_to_self_hosted(self):
        assert card(with_self_hosted=True).usd_per_minute([], is_private=True) == 0.002

    def test_a_card_predating_the_charge_still_returns_zero(self):
        """An old rate_card_version must reproduce the figure it originally published."""
        assert card(with_self_hosted=False).usd_per_minute([SELF], is_private=True) == 0.0


class TestTheTwoPathsAgree:
    """hypothetical_dollars_per_month resolves through usd_per_minute, not a private
    fallback of its own. These would have failed before the fix."""

    @staticmethod
    def ctx(labels: list[str], *, is_private: bool) -> CostContext:
        return CostContext(
            is_private=is_private,
            dominant_labels=labels,
            runs_per_month=100.0,
            rate_card=card(with_self_hosted=True),
        )

    @pytest.mark.parametrize("labels", [[HOSTED], [BIG], [SELF]])
    def test_hypothetical_equals_the_private_bill_for_the_same_runner(self, labels):
        public = self.ctx(labels, is_private=False)
        private = self.ctx(labels, is_private=True)
        assert public.hypothetical_dollars_per_month(60.0) == pytest.approx(
            private.dollars_per_month(60.0)
        )

    def test_self_hosted_hypothetical_is_not_the_hosted_linux_rate(self):
        # The old code answered 0.006/min here -- 3x the real charge.
        public = self.ctx([SELF], is_private=False)
        # 1 min/run * 100 runs * $0.002
        assert public.hypothetical_dollars_per_month(60.0) == pytest.approx(0.2)


class TestHeadlineCurrency:
    def test_public_repo_on_self_hosted_still_reports_hours_not_dollars(self):
        """A public repo pays nothing regardless of runner, so dollars stay hypothetical."""
        ctx = CostContext(
            is_private=False, dominant_labels=[SELF], runs_per_month=100.0,
            rate_card=card(with_self_hosted=True),
        )
        assert ctx.headline_currency is Currency.HOURS
        assert ctx.dollars_per_month(60.0) == 0.0

    def test_private_repo_on_self_hosted_now_reports_dollars(self):
        """The regression that mattered commercially: this used to be $0 and HOURS."""
        ctx = CostContext(
            is_private=True, dominant_labels=[SELF], runs_per_month=100.0,
            rate_card=card(with_self_hosted=True),
        )
        assert ctx.dollars_per_month(60.0) == pytest.approx(0.2)
        assert ctx.headline_currency is Currency.DOLLARS


class TestSelfHostedIsFree:
    """GitHub charges nothing for self-hosted runners.

    Card 20260301 priced the sentinel at $0.002/min on the strength of a platform charge
    that was announced, then postponed indefinitely within 48 hours, and never took
    effect. `free_on_public = true` hid the error on the corpus (all public) while it
    landed on private repos with self-hosted runners — the paying audience. See migration
    008 and CAVEATS 24.
    """

    def _card(self, self_hosted: float) -> RateCard:
        return RateCard(
            version=20260901,
            rates={"ubuntu-latest": 0.006, "__self_hosted__": self_hosted},
            free_on_public={"ubuntu-latest": True, "__self_hosted__": True},
        )

    def test_an_unknown_runner_costs_nothing_on_a_private_repo(self):
        """The regression that matters: a private repo on self-hosted hardware was being
        billed for a GitHub charge that does not exist."""
        card = self._card(0.000)
        assert card.usd_per_minute(["self-hosted", "linux"], is_private=True) == 0.0

    def test_a_known_hosted_rate_is_untouched(self):
        card = self._card(0.000)
        assert card.usd_per_minute(["ubuntu-latest"], is_private=True) == 0.006

    def test_an_old_card_still_reproduces_its_own_figure(self):
        """Cards are superseded, never rewritten — an old rate_card_version must still
        resolve through its own row, or published figures stop being auditable."""
        old = self._card(0.002)
        assert old.usd_per_minute(["self-hosted"], is_private=True) == 0.002


class TestOperatorSuppliedSelfHostedRate:
    """The gap no CI cost tool fills: GitHub bills $0 for self-hosted, so every tool
    reports $0 — while the EC2 instance is real money nobody attributes."""

    CARD = RateCard(
        version=20260901,
        rates={"ubuntu-latest": 0.006, "__self_hosted__": 0.000},
        free_on_public={"ubuntu-latest": True, "__self_hosted__": True},
    )

    def _ctx(self, labels, *, rate=None, private=True) -> CostContext:
        return CostContext(
            is_private=private, runs_per_month=100.0, rate_card=self.CARD,
            dominant_labels=labels, self_hosted_usd_per_minute=rate,
        )

    def test_unknown_means_zero_not_a_guess(self):
        assert self._ctx(["self-hosted"]).effective_rate() == 0.0

    def test_a_supplied_rate_prices_an_unrecognised_runner(self):
        assert self._ctx(["self-hosted"], rate=0.04).effective_rate() == 0.04

    def test_it_never_overrides_a_known_hosted_rate(self):
        """GitHub's bill for a hosted runner is not the operator's to restate."""
        assert self._ctx(["ubuntu-latest"], rate=0.04).effective_rate() == 0.006

    def test_it_reaches_the_dollar_figure(self):
        ctx = self._ctx(["depot-ubuntu-24.04-4"], rate=0.02)
        # 60s saved/run * 100 runs/month = 100 minutes * $0.02
        assert ctx.dollars_per_month(60.0) == pytest.approx(2.0)

    def test_a_supplied_rate_switches_the_headline_to_dollars(self):
        assert self._ctx(["self-hosted"]).headline_currency is Currency.HOURS
        assert self._ctx(["self-hosted"], rate=0.04).headline_currency is Currency.DOLLARS

    def test_a_negative_rate_is_rejected(self):
        with pytest.raises(ValueError, match="cannot be negative"):
            self._ctx(["self-hosted"], rate=-1.0)
