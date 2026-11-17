"""Long-tail findings must describe one workflow/job stream (CAVEATS 62)."""

from cadence.audit import index_step_rows
from cadence.cost import CostContext, RateCard
from cadence.detectors.context import AuditContext, StepSeries
from cadence.detectors.longtail import LongTailStepDetector
from cadence.workflow import parse_workflow

CI = ".github/workflows/ci.yml"
RELEASE = ".github/workflows/release.yml"
YAML = ("on: push\njobs:\n  build:\n    name: Build\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - name: Test\n        run: pytest\n")


def context(rows, paths=(CI, RELEASE)):
    workflows = [parse_workflow(path, YAML) for path in paths]
    legacy, resolved = index_step_rows(rows, workflows)
    return AuditContext(
        repo_id=1, owner="acme", name="widget", is_private=False,
        workflows=workflows, runs=[], step_series=legacy, step_series_resolved=resolved,
        cost=CostContext(is_private=False, runs_per_month=200.0,
                         rate_card=RateCard(version=2026, rates={}, free_on_public={})),
        window_days=90,
    )


def rows(path, seconds, start, n=30, **extra):
    return [{"workflow_path": path, "name_base": "Build", "job_name": "Build",
             "step_name": "Test", "dur": seconds, "run_id": start + i, **extra}
            for i in range(n)]


def test_identical_job_and_step_names_have_separate_identity_statistics_and_evidence():
    ctx = context(rows(CI, 300.0, 0) + rows(RELEASE, 600.0, 100))
    assert len(ctx.step_series) == 1  # the old input silently merged these streams
    found = LongTailStepDetector().run(ctx)
    assert len(found) == 2
    by_path = {f.evidence[0].file_path: f for f in found}
    for path, seconds, start, share in [(CI, 300.0, 0, 0.3333),
                                        (RELEASE, 600.0, 100, 0.6667)]:
        finding = by_path[path]
        assert finding.dedupe_key == f"long_tail_step:{path}:build:Test"
        assert finding.detector_version == "long_tail_step@2"
        assert finding.savings is None
        assert f"`build` ({path})" in finding.title
        timing, history = finding.evidence
        assert timing.payload["workflow"] == path
        assert timing.payload["job"] == "build"  # config key, not display name
        assert timing.payload["p50_seconds"] == seconds
        assert timing.payload["p95_seconds"] == seconds
        assert timing.payload["durations"] == [seconds] * 30
        assert timing.payload["observations"] == 30
        assert timing.payload["share_of_total_step_time"] == share
        assert timing.payload["eligible_series"] == 2
        assert timing.payload["total_step_seconds"] == 27_000.0
        assert history.file_path == path
        assert history.run_ids == list(range(start, start + 30))


def test_release_does_not_make_a_fast_ci_step_look_slow_or_inconsistent():
    ctx = context(rows(CI, 10.0, 0) + rows(RELEASE, 600.0, 100))
    found = LongTailStepDetector().run(ctx)
    assert len(found) == 1
    assert found[0].dedupe_key == f"long_tail_step:{RELEASE}:build:Test"
    assert found[0].evidence[0].payload["p50_seconds"] == 600.0
    assert "inconsistent" not in found[0].suggested_action


def test_two_thin_histories_cannot_pool_to_pass_the_observation_floor():
    ctx = context(rows(CI, 600.0, 0, n=10) + rows(RELEASE, 600.0, 100, n=10))
    assert len(ctx.step_series[("Build", "Test")].durations) == 20
    assert LongTailStepDetector().run(ctx) == []


def test_legacy_timings_never_substitute_for_unresolved_workflows_or_jobs():
    ctx = context(rows(CI, 600.0, 0))
    ctx.step_series_resolved.clear()
    assert ctx.step_series
    assert LongTailStepDetector().run(ctx) == []

def test_unresolvable_rows_cannot_change_a_resolved_streams_statistics_or_denominator():
    raw = (rows(CI, 300.0, 0)
           + rows(RELEASE, 9000.0, 100, job_name="Unknown", name_base="Unknown")
           + rows(None, 9000.0, 200)
           + rows("missing.yml", 9000.0, 300))
    ctx = context(raw)
    assert len(ctx.step_series_resolved) == 1
    finding, = LongTailStepDetector().run(ctx)
    assert finding.dedupe_key == f"long_tail_step:{CI}:build:Test"
    assert finding.evidence[0].payload["share_of_total_step_time"] == 1.0
    assert finding.evidence[0].payload["total_step_seconds"] == 9000.0
    assert finding.evidence[0].payload["eligible_series"] == 1
    assert finding.evidence[1].run_ids == list(range(30))
    assert "measured step time" in finding.title


def test_ranking_remains_repository_wide_not_one_winner_per_workflow():
    paths = tuple(f".github/workflows/ci{i}.yml" for i in range(10))
    ctx = context([row for i, path in enumerate(paths)
                   for row in rows(path, 600.0, i * 100)], paths=paths)
    # Each workflow has only one step, but no stream dominates the repository.
    assert LongTailStepDetector().run(ctx) == []


def test_tied_stream_order_is_stable_when_observations_arrive_in_a_different_order():
    ci, release = rows(CI, 300.0, 0), rows(RELEASE, 300.0, 100)
    left = LongTailStepDetector().run(context(ci + release))
    right = LongTailStepDetector().run(context(release + ci))
    assert [f.dedupe_key for f in left] == [f.dedupe_key for f in right]
    assert [f.dedupe_key for f in left] == [
        f"long_tail_step:{CI}:build:Test", f"long_tail_step:{RELEASE}:build:Test",
    ]


def test_identity_comes_from_resolved_index_not_legacy_series_display_fields():
    ctx = context([])
    ctx.step_series_resolved[(CI, "build", "Test")] = StepSeries(
        "Build", "different legacy name", [300.0] * 30, list(range(30)),
    )
    finding, = LongTailStepDetector().run(ctx)
    assert finding.dedupe_key == f"long_tail_step:{CI}:build:Test"
    assert finding.evidence[0].payload["step"] == "Test"
    assert finding.evidence[0].payload["job"] == "build"
