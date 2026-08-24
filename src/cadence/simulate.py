"""The counterfactual simulator.

Two modes, kept structurally apart because the credibility of every number this product
prints depends on not confusing them:

  **Replay** -- arithmetic over timings we actually observed. Removing a `needs:` edge,
  cancelling a superseded run, dropping a matrix leg. We know what each job took; we
  recompute the schedule without the change. This is not prediction.

  **Projection** -- an estimate of a state we never observed, chiefly "what if a cache
  existed here." No amount of care makes this as strong as replay, so it is reported as a
  range with its basis named, never as a point estimate.

`SavingsBasis` is a closed vocabulary matching the DB CHECK constraint. There is
deliberately no code path that adds a replay result to a projection result.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from enum import StrEnum

from cadence.dag import CriticalPath, NodeTiming, critical_path


class SavingsBasis(StrEnum):
    REPLAY = "replay"
    PROJECTION_INTRA_REPO = "projection_intra_repo"
    PROJECTION_CORPUS = "projection_corpus"

    @property
    def is_replay(self) -> bool:
        return self is SavingsBasis.REPLAY


@dataclass(slots=True)
class Savings:
    """A saving estimate. Replay carries a point value; projection carries a range.

    `low`/`high` are equal for replay -- not because the distribution is degenerate, but
    because the arithmetic is exact for the runs observed.
    """

    seconds_per_run: float
    basis: SavingsBasis
    low: float
    high: float
    n_runs: int
    detail: str = ""

    @property
    def is_range(self) -> bool:
        return not self.basis.is_replay

    def render(self) -> str:
        if self.is_range:
            return f"{_mmss(self.low)}–{_mmss(self.high)}/run (estimated)"
        return f"{_mmss(self.seconds_per_run)}/run (measured, n={self.n_runs})"


def _mmss(seconds: float) -> str:
    seconds = max(0.0, seconds)
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


# ─────────────────────────────────────────────────────────── replay


def replay_without_edge(
    edges: dict[str, list[str]],
    timings: dict[str, NodeTiming],
    *,
    from_node: str,
    to_node: str,
) -> tuple[CriticalPath | None, CriticalPath | None]:
    """Critical path (before, after) removing the `to_node needs: from_node` edge."""
    before = critical_path(edges, timings)
    stripped = {k: [d for d in deps if not (k == to_node and d == from_node)]
                for k, deps in edges.items()}
    after = critical_path(stripped, timings)
    return before, after


def replay_edge_removal_savings(
    per_run: list[tuple[dict[str, list[str]], dict[str, NodeTiming]]],
    *,
    from_node: str,
    to_node: str,
) -> Savings | None:
    """Replay an edge removal across many runs and summarise the delta.

    Runs where the edge was not on the critical path yield a zero delta and are kept in
    the sample rather than filtered out -- dropping them would inflate the mean into a
    best-case figure rather than an expected one.
    """
    deltas: list[float] = []
    for edges, timings in per_run:
        before, after = replay_without_edge(edges, timings, from_node=from_node, to_node=to_node)
        if before is None or after is None:
            continue
        deltas.append(max(0.0, before.total_seconds - after.total_seconds))

    if not deltas:
        return None

    median = statistics.median(deltas)
    return Savings(
        seconds_per_run=median,
        basis=SavingsBasis.REPLAY,
        low=median,
        high=median,
        n_runs=len(deltas),
        detail=f"p50 across {len(deltas)} runs; max {_mmss(max(deltas))}",
    )


@dataclass(slots=True)
class SupersededRun:
    run_id: int
    superseded_by: int
    wasted_seconds: float


def find_superseded_runs(
    runs: list[tuple[int, str, float, float]],
) -> list[SupersededRun]:
    """Runs that kept executing after a newer run started on the same ref.

    `runs` is (run_id, ref, started_epoch, completed_epoch), any order.

    This is exact replay, not estimation: with `cancel-in-progress` the older run would
    have been killed the instant the newer one started, so the wasted span is precisely
    the overlap. Concurrent runs on *different* refs are untouched -- they are not
    superseding each other.
    """
    by_ref: dict[str, list[tuple[int, float, float]]] = {}
    for run_id, ref, started, completed in runs:
        if ref is None or started is None or completed is None:
            continue
        by_ref.setdefault(ref, []).append((run_id, started, completed))

    out: list[SupersededRun] = []
    for entries in by_ref.values():
        entries.sort(key=lambda e: e[1])
        for i, (run_id, _started, completed) in enumerate(entries):
            # The first later-starting run that began before this one finished.
            for later_id, later_started, _later_completed in entries[i + 1:]:
                if later_started >= completed:
                    break
                out.append(
                    SupersededRun(
                        run_id=run_id,
                        superseded_by=later_id,
                        wasted_seconds=completed - later_started,
                    )
                )
                break
    return out


def replay_cancellation_savings(superseded: list[SupersededRun], total_runs: int) -> Savings | None:
    """Wasted compute per run, amortised over every run in the window.

    Amortising over *all* runs rather than only superseded ones matters: the fix is
    repo-wide, so its value is the average it returns per run, not the dramatic figure
    from the subset that happened to be superseded.
    """
    if not superseded or total_runs <= 0:
        return None
    total_wasted = sum(s.wasted_seconds for s in superseded)
    per_run = total_wasted / total_runs
    return Savings(
        seconds_per_run=per_run,
        basis=SavingsBasis.REPLAY,
        low=per_run,
        high=per_run,
        n_runs=total_runs,
        detail=(
            f"{len(superseded)} superseded runs wasted {_mmss(total_wasted)} total "
            f"across {total_runs} runs"
        ),
    )


# ─────────────────────────────────────────────────────────── projection

# Hit rate is dominated by key composition. Published figures, used only to project a
# state we cannot observe -- never mixed into a replay result.
CACHE_HIT_PRIORS: dict[str, float] = {
    "run_id": 0.00,      # a key containing github.run_id is written every run, read never
    "branch": 0.30,
    "branch_os": 0.45,
    "branch_hashfiles": 0.75,
    "os_hashfiles": 0.90,
    "setup_builtin": 0.95,
}


def project_cache_savings(
    install_durations: list[float],
    *,
    assumed_hit_rate: float = 0.90,
    restore_fraction_low: float = 0.05,
    restore_fraction_high: float = 0.20,
) -> Savings | None:
    """Estimate time recovered by adding a dependency cache where none exists.

    We have no observation of the cached state for this repo, so this is explicitly a
    projection. The range comes from how much of the install a warm restore still costs
    -- 5% to 20% of the cold install, which spans the realistic spread across ecosystems
    rather than pretending a single ratio applies to npm, pip, and cargo alike.
    """
    usable = [d for d in install_durations if d is not None and d > 0]
    if len(usable) < 5:
        return None

    baseline = statistics.median(usable)
    high = baseline * assumed_hit_rate * (1 - restore_fraction_low)
    low = baseline * assumed_hit_rate * (1 - restore_fraction_high)
    return Savings(
        seconds_per_run=(low + high) / 2,
        basis=SavingsBasis.PROJECTION_CORPUS,
        low=low,
        high=high,
        n_runs=len(usable),
        detail=(
            f"cold install p50 {_mmss(baseline)} across {len(usable)} runs; "
            f"assumes {assumed_hit_rate:.0%} hit rate"
        ),
    )


def duration_is_flat(durations: list[float], *, cv_threshold: float = 0.25) -> bool:
    """Whether a step's duration lacks the bimodality a working cache produces.

    A cached install is fast on hit and slow on miss, so its spread is wide. A
    consistently slow install with low relative variance is the signature of no cache at
    all -- which is what distinguishes "no cache configured" from "cache configured but
    thrashing".
    """
    usable = [d for d in durations if d is not None and d > 0]
    if len(usable) < 5:
        return False
    mean = statistics.fmean(usable)
    if mean <= 0:
        return False
    return (statistics.pstdev(usable) / mean) < cv_threshold
