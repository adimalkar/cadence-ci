"""Job DAG, levelling, and critical path over *observed* timings.

The graph comes from the workflow's `needs:` edges; the weights come from real recorded
job durations. Neither alone is enough -- config without timings cannot say what is slow,
and timings without config cannot say what was *waiting* versus merely later.

Queue time is carried separately throughout and never folded into execution time. A
queue-bound repo gets the opposite advice from a compute-bound one, and merging the two
is how a tool ends up telling someone to parallelise a pipeline that is already starved
of runners.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class NodeTiming:
    """Observed timing for one DAG node, aggregated across its matrix legs.

    Legs run in parallel, so the node's contribution to the critical path is its
    *slowest* leg -- not the sum, which would be billable time rather than elapsed.
    """

    key: str
    queue_seconds: float
    exec_seconds: float
    leg_count: int = 1

    @property
    def total_seconds(self) -> float:
        return self.queue_seconds + self.exec_seconds


@dataclass(slots=True)
class CriticalPath:
    total_seconds: float
    path: list[str]
    node_finish: dict[str, float] = field(default_factory=dict)

    @property
    def bottleneck(self) -> str | None:
        return self.path[-1] if self.path else None


def toposort(edges: dict[str, list[str]]) -> list[str] | None:
    """Kahn's algorithm. Returns None on a cycle rather than raising.

    GitHub rejects cyclic `needs:` graphs, so a cycle here means we mis-parsed or the
    workflow references a job that does not exist -- either way the correct response is
    to decline to analyse, not to emit a confident wrong number.
    """
    indegree = {node: 0 for node in edges}
    for node, deps in edges.items():
        for dep in deps:
            if dep in indegree:
                indegree[node] += 1

    queue = sorted([n for n, d in indegree.items() if d == 0])
    order: list[str] = []
    remaining = {n: list(deps) for n, deps in edges.items()}

    while queue:
        node = queue.pop(0)
        order.append(node)
        for other, deps in remaining.items():
            if node in deps:
                deps.remove(node)
                indegree[other] -= 1
                if indegree[other] == 0:
                    queue.append(other)
        queue.sort()

    return order if len(order) == len(edges) else None


def critical_path(
    edges: dict[str, list[str]], timings: dict[str, NodeTiming]
) -> CriticalPath | None:
    """Longest weighted path through the DAG using observed durations.

    A node starts when its slowest dependency finishes, then pays its own queue wait and
    execution. Nodes with no recorded timing are treated as zero-cost pass-throughs --
    they preserve ordering without inventing duration for a job we never observed.
    """
    order = toposort(edges)
    if order is None:
        return None

    finish: dict[str, float] = {}
    predecessor: dict[str, str | None] = {}

    for node in order:
        deps = [d for d in edges.get(node, []) if d in finish]
        if deps:
            blocking = max(deps, key=lambda d: finish[d])
            start = finish[blocking]
            predecessor[node] = blocking
        else:
            start = 0.0
            predecessor[node] = None

        timing = timings.get(node)
        finish[node] = start + (timing.total_seconds if timing else 0.0)

    if not finish:
        return None

    end = max(finish, key=lambda n: finish[n])
    path: list[str] = []
    cursor: str | None = end
    while cursor is not None:
        path.append(cursor)
        cursor = predecessor.get(cursor)
    path.reverse()

    return CriticalPath(total_seconds=finish[end], path=path, node_finish=finish)


def theoretical_floor(timings: dict[str, NodeTiming]) -> float:
    """The fastest this pipeline could finish with unlimited parallelism and no edges.

    A run can never beat its single slowest job, so this is the hard floor every
    scheduling fix is measured against.
    """
    if not timings:
        return 0.0
    return max(t.total_seconds for t in timings.values())


def aggregate_spans(
    rows: list[tuple[str, float | None, float | None, float | None]],
) -> dict[str, NodeTiming]:
    """Collapse observed jobs into node timings by **elapsed span**.

    `rows` is (node_key, created_epoch, started_epoch, completed_epoch).

    Span rather than max-of-durations, because a node can contain jobs that run
    *sequentially*: a reusable-workflow call (`X / a`, `X / b`, …) has its own internal
    `needs:` graph, so taking the longest inner job would understate the time the caller
    node actually occupied. For matrix legs, which do start together, span and max agree.

    The decomposition stays additive: queue is the wait before anything started, exec is
    from first start to last finish, and the two sum to the span.
    """
    # Coerce at the boundary: psycopg returns `extract(epoch ...)` as Decimal, and a
    # Decimal reaching NodeTiming poisons every downstream float sum with a TypeError.
    # A detector that raises is caught and logged upstream, which means this failure mode
    # silently disables a rule rather than announcing itself -- so normalise here, once.
    def _f(v: object) -> float | None:
        return None if v is None else float(v)  # type: ignore[arg-type]

    grouped: dict[str, list[tuple[float | None, float | None, float | None]]] = {}
    for key, created, started, completed in rows:
        grouped.setdefault(key, []).append((_f(created), _f(started), _f(completed)))

    out: dict[str, NodeTiming] = {}
    for key, entries in grouped.items():
        created = [c for c, _, _ in entries if c is not None]
        started = [s for _, s, _ in entries if s is not None]
        completed = [x for _, _, x in entries if x is not None]
        if not started or not completed:
            continue
        first_start = min(started)
        queue = max(0.0, first_start - min(created)) if created else 0.0
        exec_s = max(0.0, max(completed) - first_start)
        out[key] = NodeTiming(
            key=key, queue_seconds=queue, exec_seconds=exec_s, leg_count=len(entries)
        )
    return out


def aggregate_legs(rows: list[tuple[str, float, float]]) -> dict[str, NodeTiming]:
    """Collapse per-leg job rows into per-node timings.

    `rows` is (node_key, queue_seconds, exec_seconds). Matrix legs execute concurrently,
    so the node takes the max of its legs -- summing would conflate elapsed time with
    billable time, the single most common error in CI cost analysis.
    """
    out: dict[str, NodeTiming] = {}
    for key, queue_s, exec_s in rows:
        existing = out.get(key)
        if existing is None:
            out[key] = NodeTiming(key=key, queue_seconds=queue_s, exec_seconds=exec_s)
        else:
            if queue_s + exec_s > existing.total_seconds:
                existing.queue_seconds = queue_s
                existing.exec_seconds = exec_s
            existing.leg_count += 1
    return out
