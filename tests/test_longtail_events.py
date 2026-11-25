"""Long-tail statistics are computed within one triggering event (CAVEATS 83).

A guard step that takes half a minute on a pull request and half an hour on a push to main
must not be reported with a blended median -- that number describes neither run.
Concentration, by contrast, stays measured against all step time.
"""

from cadence.cost import CostContext, RateCard
from cadence.detectors.context import AuditContext, RunObservation, StepSeries
from cadence.detectors.longtail import LongTailStepDetector, split_by_event
from cadence.workflow import parse_workflow

CI = ".github/workflows/ci.yml"
YAML = ("on: [push, pull_request]\njobs:\n  check:\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - name: Guards\n        run: ./guards\n"
        "      - name: Lint\n        run: ./lint\n")


def run(run_id: int, event: str | None) -> RunObservation:
    return RunObservation(run_id=run_id, head_branch="main", started_epoch=0.0,
                          completed_epoch=1.0, conclusion="success", workflow_path=CI,
                          event=event)


def context(streams: dict[str, list[tuple[int, float]]],
            events: dict[int, str | None]) -> AuditContext:
    """streams: step name -> [(run_id, seconds)]; events: run_id -> event."""
    resolved = {
        (CI, "check", step): StepSeries("check", step, [d for _, d in obs],
                                        [rid for rid, _ in obs])
        for step, obs in streams.items()
    }
    return AuditContext(
        repo_id=1, owner="acme", name="widget", is_private=False,
        workflows=[parse_workflow(CI, YAML)],
        runs=[run(rid, ev) for rid, ev in events.items()],
        step_series={}, step_series_resolved=resolved,
        cost=CostContext(is_private=False, runs_per_month=200.0,
                         rate_card=RateCard(version=2026, rates={}, free_on_public={})),
        window_days=90,
    )


PUSH = range(0, 30)
PRS = range(100, 160)
# guard_split's step time: Guards on push and on PRs, plus Lint on every run.
TOTAL = 30 * 1800.0 + 60 * 36.0 + 90 * 30.0


def guard_split(push_s=1800.0, pr_s=36.0, lint_s=30.0):
    events = {**{i: "push" for i in PUSH}, **{i: "pull_request" for i in PRS}}
    streams = {
        "Guards": [(i, push_s) for i in PUSH] + [(i, pr_s) for i in PRS],
        "Lint": [(i, lint_s) for i in (*PUSH, *PRS)],
    }
    return context(streams, events)


class TestTheMedianIsTakenWithinOneEvent:
    def test_a_blended_median_would_have_described_the_pr_runs(self):
        ctx = guard_split()
        blended = sorted(ctx.step_series_resolved[(CI, "check", "Guards")].durations)
        assert blended[len(blended) // 2] == 36.0  # @2 reported this as the p50

        finding, = LongTailStepDetector().run(ctx)
        timing = finding.evidence[0].payload
        assert timing["event"] == "push"
        assert timing["p50_seconds"] == 1800.0
        assert timing["observations"] == 30
        assert "(`push` runs: p50 30:00, p95 30:00" in finding.title

    def test_the_fast_event_is_named_as_a_contrast(self):
        finding, = LongTailStepDetector().run(guard_split())
        assert "but `pull_request` p50 0:36)" in finding.title
        assert "depends on the trigger" in finding.suggested_action
        by_event = {e["event"]: e for e in finding.evidence[0].payload["by_event"]}
        assert by_event["push"]["flagged"] is True
        assert by_event["pull_request"] == {
            "event": "pull_request", "flagged": False, "observations": 60,
            "p50_seconds": 36.0, "p95_seconds": 36.0,
            "share_of_total_step_time": round(60 * 36 / TOTAL, 4),
        }

    def test_mixing_events_no_longer_reads_as_inconsistency(self):
        finding, = LongTailStepDetector().run(guard_split())
        assert "inconsistent" not in finding.suggested_action

    def test_share_is_still_of_all_measured_step_time(self):
        finding, = LongTailStepDetector().run(guard_split())
        timing = finding.evidence[0].payload
        assert timing["total_step_seconds"] == TOTAL
        assert timing["share_of_total_step_time"] == round((30 * 1800 + 60 * 36) / TOTAL, 4)
        assert f"is {(30 * 1800 + 60 * 36) / TOTAL:.0%} of measured step time" in finding.title

    def test_a_step_concentrated_only_within_a_small_event_is_not_a_long_tail(self):
        # Dividing by each event's own step time would make this step 100% of `push`.
        events = {**{i: "push" for i in PUSH}, **{i: "pull_request" for i in PRS}}
        ctx = context({"Guards": [(i, 120.0) for i in PUSH],
                       "Lint": [(i, 1000.0) for i in PRS]}, events)
        finding, = LongTailStepDetector().run(ctx)
        assert finding.evidence[0].payload["step"] == "Lint"

    def test_run_history_cites_only_flagged_events(self):
        finding, = LongTailStepDetector().run(guard_split())
        assert finding.evidence[1].run_ids == list(PUSH)


class TestIdentity:
    def test_one_finding_per_step_even_when_slow_in_several_events(self):
        events = {**{i: "push" for i in PUSH}, **{i: "schedule" for i in PRS}}
        ctx = context({"Guards": [(i, 1800.0) for i in PUSH] + [(i, 1600.0) for i in PRS]},
                      events)
        finding, = LongTailStepDetector().run(ctx)
        assert finding.dedupe_key == f"long_tail_step:{CI}:check:Guards"  # unchanged from @2
        assert finding.detector_version == "long_tail_step@3"
        assert finding.evidence[0].payload["event"] == "schedule"  # more total time
        assert "(`schedule` runs: p50 26:40, p95 26:40; `push` p50 30:00)" in finding.title
        assert finding.evidence[1].run_ids == list(PRS)[:50]  # primary event first, capped

    def test_a_step_slow_only_on_prs_is_reported_for_prs(self):
        finding, = LongTailStepDetector().run(guard_split(push_s=36.0, pr_s=900.0))
        assert finding.evidence[0].payload["event"] == "pull_request"
        assert "but `push` p50 0:36)" in finding.title


class TestTheObservationFloorIsPerEvent:
    def test_two_thin_event_histories_cannot_pool(self):
        events = {**{i: "push" for i in range(12)}, **{i: "pull_request" for i in range(100, 112)}}
        ctx = context({"Guards": [(i, 600.0) for i in events]}, events)
        assert len(ctx.step_series_resolved[(CI, "check", "Guards")].durations) == 24
        assert LongTailStepDetector().run(ctx) == []

    def test_a_thin_fast_event_is_not_claimed_as_a_contrast(self):
        events = {**{i: "push" for i in PUSH}, **{i: "pull_request" for i in range(100, 105)}}
        ctx = context({"Guards": [(i, 600.0) for i in PUSH]
                       + [(i, 10.0) for i in range(100, 105)]}, events)
        finding, = LongTailStepDetector().run(ctx)
        assert "pull_request" not in finding.title
        assert {e["event"] for e in finding.evidence[0].payload["by_event"]} == {
            "push", "pull_request"}


class TestUnknownEvents:
    def test_without_events_the_finding_matches_the_unsegmented_rule(self):
        ctx = context({"Guards": [(i, 600.0) for i in PUSH]}, {i: None for i in PUSH})
        finding, = LongTailStepDetector().run(ctx)
        assert finding.evidence[0].payload["event"] is None
        assert " runs (" not in finding.title
        assert finding.title.endswith("is 100% of measured step time (p50 10:00, p95 10:00)")

    def test_a_run_missing_from_the_context_counts_as_unknown(self):
        ctx = context({"Guards": [(i, 600.0) for i in PUSH]}, {})
        parts = split_by_event(ctx)[(CI, "check", "Guards")]
        assert list(parts) == [None]

    def test_misaligned_run_ids_are_not_guessed_at(self):
        ctx = context({"Guards": [(i, 600.0) for i in PUSH]}, {i: "push" for i in PUSH})
        ctx.step_series_resolved[(CI, "check", "Guards")].run_ids.pop()
        parts = split_by_event(ctx)[(CI, "check", "Guards")]
        assert list(parts) == [None]
        assert len(parts[None].durations) == 30
