from __future__ import annotations

from cadence.dag import (
    NodeTiming,
    aggregate_legs,
    critical_path,
    theoretical_floor,
    toposort,
)
from cadence.simulate import (
    SavingsBasis,
    duration_is_flat,
    find_superseded_runs,
    project_cache_savings,
    replay_cancellation_savings,
    replay_edge_removal_savings,
    replay_without_edge,
)


def t(key: str, exec_s: float, queue_s: float = 0.0) -> NodeTiming:
    return NodeTiming(key=key, queue_seconds=queue_s, exec_seconds=exec_s)


class TestToposort:
    def test_linear_chain(self):
        assert toposort({"a": [], "b": ["a"], "c": ["b"]}) == ["a", "b", "c"]

    def test_cycle_returns_none_rather_than_raising(self):
        """GitHub rejects cyclic needs:, so a cycle means we mis-parsed. Declining to
        analyse beats emitting a confident wrong number."""
        assert toposort({"a": ["b"], "b": ["a"]}) is None

    def test_dangling_dependency_is_tolerated(self):
        assert toposort({"a": ["ghost"], "b": []}) is not None


class TestCriticalPath:
    def test_longest_path_not_the_sum(self):
        # a → b, a → c ; b and c run in parallel after a
        edges = {"a": [], "b": ["a"], "c": ["a"]}
        timings = {"a": t("a", 10), "b": t("b", 30), "c": t("c", 5)}
        cp = critical_path(edges, timings)
        assert cp.total_seconds == 40  # 10 + 30, not 45
        assert cp.path == ["a", "b"]
        assert cp.bottleneck == "b"

    def test_queue_time_counts_toward_the_path(self):
        edges = {"a": []}
        cp = critical_path(edges, {"a": t("a", 10, queue_s=20)})
        assert cp.total_seconds == 30

    def test_untimed_node_is_a_passthrough_not_invented_duration(self):
        edges = {"a": [], "b": ["a"]}
        cp = critical_path(edges, {"b": t("b", 10)})  # 'a' never observed
        assert cp.total_seconds == 10

    def test_cycle_yields_none(self):
        assert critical_path({"a": ["b"], "b": ["a"]}, {}) is None


class TestFloorAndLegs:
    def test_floor_is_the_slowest_single_job(self):
        timings = {"a": t("a", 10), "b": t("b", 45), "c": t("c", 20)}
        assert theoretical_floor(timings) == 45

    def test_matrix_legs_collapse_to_slowest_not_sum(self):
        """Legs run concurrently. Summing them would report billable time as elapsed --
        the most common error in CI cost analysis."""
        node = aggregate_legs([("test", 0, 100), ("test", 0, 300), ("test", 0, 200)])
        assert node["test"].exec_seconds == 300
        assert node["test"].leg_count == 3


class TestReplayEdgeRemoval:
    def test_removing_a_blocking_edge_shortens_the_run(self):
        edges = {"a": [], "b": ["a"]}
        timings = {"a": t("a", 100), "b": t("b", 50)}
        before, after = replay_without_edge(edges, timings, from_node="a", to_node="b")
        assert before.total_seconds == 150
        assert after.total_seconds == 100  # b now runs beside a

    def test_removing_a_non_blocking_edge_changes_nothing(self):
        """b is not on the critical path, so unblocking it saves zero -- and the rule
        must not claim otherwise."""
        edges = {"a": [], "b": ["a"], "c": []}
        timings = {"a": t("a", 10), "b": t("b", 5), "c": t("c", 500)}
        before, after = replay_without_edge(edges, timings, from_node="a", to_node="b")
        assert before.total_seconds == after.total_seconds == 500

    def test_savings_are_replay_basis_with_a_point_value(self):
        per_run = [({"a": [], "b": ["a"]}, {"a": t("a", 100), "b": t("b", 50)})] * 10
        s = replay_edge_removal_savings(per_run, from_node="a", to_node="b")
        assert s.basis is SavingsBasis.REPLAY
        assert s.basis.is_replay
        assert s.low == s.high == s.seconds_per_run == 50
        assert not s.is_range
        assert s.n_runs == 10

    def test_zero_delta_runs_are_kept_in_the_sample(self):
        """Filtering them out would turn an expected value into a best case."""
        blocking = ({"a": [], "b": ["a"]}, {"a": t("a", 100), "b": t("b", 50)})
        neutral = ({"a": [], "b": ["a"]}, {"a": t("a", 100), "b": t("b", 1)})
        s = replay_edge_removal_savings([blocking] * 5 + [neutral] * 5,
                                        from_node="a", to_node="b")
        assert s.n_runs == 10
        assert 0 < s.seconds_per_run < 50


class TestSupersededRuns:
    def test_overlap_on_same_ref_is_detected(self):
        # run 1 starts at 0 ends at 100; run 2 starts at 40 -> 60s wasted
        runs = [(1, "main", 0.0, 100.0), (2, "main", 40.0, 140.0)]
        out = find_superseded_runs(runs)
        assert len(out) == 1
        assert out[0].run_id == 1
        assert out[0].superseded_by == 2
        assert out[0].wasted_seconds == 60.0

    def test_different_refs_never_supersede_each_other(self):
        runs = [(1, "main", 0.0, 100.0), (2, "feature", 40.0, 140.0)]
        assert find_superseded_runs(runs) == []

    def test_sequential_runs_are_not_superseded(self):
        runs = [(1, "main", 0.0, 50.0), (2, "main", 60.0, 100.0)]
        assert find_superseded_runs(runs) == []

    def test_savings_amortise_over_all_runs_not_just_superseded(self):
        """The fix is repo-wide, so its value is the average per run -- quoting the
        subset figure would overstate it."""
        superseded = find_superseded_runs([(1, "main", 0.0, 100.0), (2, "main", 40.0, 140.0)])
        s = replay_cancellation_savings(superseded, total_runs=10)
        assert s.seconds_per_run == 6.0  # 60s wasted / 10 runs
        assert s.basis is SavingsBasis.REPLAY


class TestProjection:
    def test_cache_projection_is_a_range_never_a_point(self):
        s = project_cache_savings([100.0] * 20)
        assert s.basis is SavingsBasis.PROJECTION_CORPUS
        assert not s.basis.is_replay
        assert s.is_range
        assert s.low < s.high

    def test_projection_needs_enough_observations(self):
        assert project_cache_savings([100.0, 100.0]) is None

    def test_render_distinguishes_measured_from_estimated(self):
        """A reader must be able to tell replay from projection without reading the
        basis field -- this is the product's core credibility rule."""
        replay = replay_edge_removal_savings(
            [({"a": [], "b": ["a"]}, {"a": t("a", 100), "b": t("b", 50)})] * 5,
            from_node="a", to_node="b",
        )
        projected = project_cache_savings([100.0] * 20)
        assert "measured" in replay.render()
        assert "estimated" in projected.render()
        assert "–" in projected.render()  # a range, not a point


class TestFlatness:
    def test_flat_durations_indicate_no_cache(self):
        assert duration_is_flat([100, 102, 98, 101, 99, 100]) is True

    def test_bimodal_durations_indicate_a_working_cache(self):
        """Fast on hit, slow on miss -- wide spread means something is being restored."""
        assert duration_is_flat([100, 5, 98, 6, 102, 5]) is False

    def test_too_few_samples_is_not_flat(self):
        assert duration_is_flat([100, 100]) is False


class TestAggregateSpans:
    """Span-based aggregation, needed because a reusable-workflow node contains jobs
    that may run sequentially -- max-of-durations would understate the node."""

    def test_sequential_inner_jobs_use_the_full_span(self):
        from cadence.dag import aggregate_spans

        # two inner jobs, back to back: 0-50 then 50-120
        rows = [("build", 0.0, 0.0, 50.0), ("build", 0.0, 50.0, 120.0)]
        node = aggregate_spans(rows)["build"]
        assert node.exec_seconds == 120.0  # not 70 (the longest single job)
        assert node.leg_count == 2

    def test_parallel_legs_agree_with_max(self):
        from cadence.dag import aggregate_spans

        rows = [("test", 0.0, 0.0, 100.0), ("test", 0.0, 0.0, 300.0)]
        assert aggregate_spans(rows)["test"].exec_seconds == 300.0

    def test_queue_and_exec_sum_to_the_span(self):
        from cadence.dag import aggregate_spans

        node = aggregate_spans([("a", 0.0, 20.0, 120.0)])["a"]
        assert node.queue_seconds == 20.0
        assert node.exec_seconds == 100.0
        assert node.total_seconds == 120.0

    def test_incomplete_rows_are_dropped_not_guessed(self):
        from cadence.dag import aggregate_spans

        assert aggregate_spans([("a", 0.0, None, None)]) == {}
