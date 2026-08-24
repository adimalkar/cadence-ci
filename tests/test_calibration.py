from __future__ import annotations

from cadence.calibration import BasisReport, Observation, Outcome, report


def obs(pred: float, real: float | None, basis="replay", kind="k") -> Observation:
    return Observation(finding_id="f", repo="a/b", kind=kind, basis=basis,
                       predicted_seconds=pred, realized_seconds=real)


class TestOutcome:
    def test_exact_prediction_is_within_band(self):
        assert obs(100, 100).outcome is Outcome.WITHIN_BAND

    def test_edge_of_band_counts_as_calibrated(self):
        assert obs(100, 125).outcome is Outcome.WITHIN_BAND
        assert obs(100, 75).outcome is Outcome.WITHIN_BAND

    def test_promising_more_than_delivered_is_overestimated(self):
        """The direction that matters most -- it is the one that loses trust."""
        assert obs(100, 40).outcome is Outcome.OVERESTIMATED

    def test_delivering_more_than_promised_is_underestimated(self):
        assert obs(100, 200).outcome is Outcome.UNDERESTIMATED

    def test_unapplied_fix_is_pending_not_a_failure(self):
        assert obs(100, None).outcome is Outcome.PENDING

    def test_signed_error_direction(self):
        assert obs(100, 50).signed_error == -0.5
        assert obs(100, 150).signed_error == 0.5
        assert obs(100, None).signed_error is None


class TestReport:
    def test_replay_and_projection_are_never_combined(self):
        """PRODUCT.md section 9: replay should be near-exact and projection is where the
        error lives. Blending them hides the only useful signal."""
        rep = report([
            obs(100, 100, basis="replay"),
            obs(100, 100, basis="replay"),
            obs(100, 30, basis="projection_corpus"),
        ])
        assert set(rep) == {"replay", "projection_corpus"}
        assert rep["replay"].calibration == 1.0
        assert rep["projection_corpus"].calibration == 0.0

    def test_unmeasured_basis_reports_none_not_zero(self):
        """'We have not checked' and 'we checked and were wrong' are different claims."""
        rep = report([obs(100, None)])
        assert rep["replay"].measured == 0
        assert rep["replay"].pending == 1
        assert rep["replay"].calibration is None

    def test_pending_excluded_from_the_denominator(self):
        rep = report([obs(100, 100), obs(100, 100), obs(100, None)])
        assert rep["replay"].measured == 2
        assert rep["replay"].pending == 1
        assert rep["replay"].calibration == 1.0

    def test_median_signed_error_is_reported(self):
        rep = report([obs(100, 50), obs(100, 60), obs(100, 40)])
        # errors: -0.5, -0.4, -0.6 -> median -0.5
        assert rep["replay"].median_signed_error == -0.5

    def test_kinds_are_counted_for_per_rule_breakdown(self):
        rep = report([obs(100, 100, kind="cache"), obs(100, 100, kind="cache"),
                      obs(100, 100, kind="cancel")])
        assert rep["replay"].kinds == {"cache": 2, "cancel": 1}

    def test_empty_input_yields_empty_report(self):
        assert report([]) == {}


class TestBasisReport:
    def test_calibration_none_on_empty(self):
        assert BasisReport(basis="replay").calibration is None
