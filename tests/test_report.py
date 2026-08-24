from __future__ import annotations

from cadence.cost import RateCard
from cadence.detectors.base import EvidenceDraft, FindingDraft
from cadence.report import ReportModel, render_html, report_json
from cadence.simulate import Savings, SavingsBasis

RC = RateCard(version=2026, rates={"ubuntu-latest": 0.006},
              free_on_public={"ubuntu-latest": True})


def _draft(kind: str, basis: SavingsBasis | None, secs: float = 60.0) -> FindingDraft:
    savings = None
    if basis is not None:
        savings = Savings(seconds_per_run=secs, basis=basis, low=secs * 0.8,
                          high=secs * 1.2, n_runs=42, detail="detail text")
    return FindingDraft(
        kind=kind, module="waste", severity=3, confidence=0.9,
        dedupe_key=f"{kind}:k", title=f"title for {kind}", detector_version="v1",
        savings=savings, suggested_action="do the thing",
        evidence=[EvidenceDraft(kind="code_range", file_path="a/ci.yml", line_start=12),
                  EvidenceDraft(kind="run_history", run_ids=[1, 2, 3])],
    )


def _model(**over) -> ReportModel:
    base = dict(
        repo="acme/widget", is_private=False, runs=100,
        workflow=".github/workflows/ci.yml", coverage=1.0, wall_seconds=600.0,
        critical_path_seconds=500.0, floor_seconds=400.0, queue_bound=False,
        findings=[], replay_total=0.0, headline="",
    )
    base.update(over)
    return ReportModel(**base)


class TestBasisIsVisuallyDistinct:
    """PRODUCT.md section 6: a reader must tell replay from projection without reading
    the words. Same-looking output would undermine every number on the page."""

    def test_replay_renders_solid_swatch_and_point_value(self):
        html = render_html(_model(findings=[_draft("a", SavingsBasis.REPLAY)],
                                 replay_total=60.0))
        assert "sw--replay" in html
        assert "replay · measured" in html
        assert "–" not in html.split('class="save')[1][:80]  # point value, not a range

    def test_projection_renders_hatched_swatch_and_a_range(self):
        html = render_html(_model(findings=[_draft("b", SavingsBasis.PROJECTION_CORPUS)]))
        assert "sw--proj" in html
        assert "projection · estimated" in html
        assert "–" in html  # a range

    def test_both_bases_on_one_page_stay_distinguishable(self):
        html = render_html(_model(
            findings=[_draft("a", SavingsBasis.REPLAY),
                      _draft("b", SavingsBasis.PROJECTION_CORPUS)],
            replay_total=60.0))
        assert "sw--replay" in html and "sw--proj" in html


class TestHonestyGates:
    def test_waterfall_withheld_when_mapping_coverage_is_low(self):
        """The wall-clock-vs-floor gap would read as recoverable time when it is really
        jobs we could not place."""
        html = render_html(_model(coverage=0.18,
                                  findings=[_draft("a", SavingsBasis.REPLAY)],
                                  replay_total=60.0))
        assert "Partial analysis" in html
        assert 'class="hatch"' not in html

    def test_waterfall_shown_when_well_mapped(self):
        html = render_html(_model(findings=[_draft("a", SavingsBasis.REPLAY)],
                                  replay_total=60.0))
        assert 'class="hatch"' in html
        assert "Partial analysis" not in html

    def test_queue_bound_says_parallelism_would_hurt(self):
        html = render_html(_model(queue_bound=True))
        assert "Queue-bound" in html
        assert "slower, not faster" in html

    def test_only_replay_reaches_the_headline_total(self):
        m = _model(findings=[_draft("a", SavingsBasis.PROJECTION_CORPUS)], replay_total=0.0)
        assert m.recoverable_pct == 0.0


class TestEmptyState:
    def test_no_findings_is_a_real_outcome_not_a_failure(self):
        html = render_html(_model())
        assert "No recoverable waste found" in html
        assert "tight" in html


class TestEscaping:
    def test_titles_are_html_escaped(self):
        d = _draft("x", SavingsBasis.REPLAY)
        d.title = '<script>alert("xss")</script>'
        html = render_html(_model(findings=[d], replay_total=60.0))
        assert "<script>alert" not in html
        assert "&lt;script&gt;" in html


class TestJson:
    def test_json_twin_carries_basis_and_evidence_kinds(self):
        import json
        payload = json.loads(report_json(_model(
            findings=[_draft("a", SavingsBasis.REPLAY)], replay_total=60.0)))
        assert payload["repo"] == "acme/widget"
        f = payload["findings"][0]
        assert f["basis"] == "replay"
        assert set(f["evidence_kinds"]) == {"code_range", "run_history"}
