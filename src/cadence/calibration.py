"""Savings calibration — the metric that makes the numbers checkable.

The deterministic rules are ~100% precise by construction (a cache is either configured
or it is not), so precision is the wrong headline. The real question is whether the
*savings figures* are true, and the only honest way to answer it is to compare what we
predicted against what actually happened after a fix landed.

`PRODUCT.md` §9 defines the target: of merged fix PRs, the share whose realized saving
lands within ±25% of predicted, **reported separately for replay and projection**.
Blending them would hide the fact that replay should be near-exact while projection is
where the error lives — which is the single most useful thing this metric can tell us.

Nothing here invents a number when a fix has not been applied. A finding with no
`realized_*` value is `PENDING`, and pending is a valid, reportable state.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from enum import StrEnum

import psycopg
from psycopg.rows import dict_row

WITHIN = 0.25  # the ±band that counts as calibrated


class Outcome(StrEnum):
    WITHIN_BAND = "within_band"
    OVERESTIMATED = "overestimated"   # we promised more than was delivered
    UNDERESTIMATED = "underestimated"
    PENDING = "pending"               # fix not applied, or not yet measured


@dataclass(slots=True)
class Observation:
    finding_id: str
    repo: str
    kind: str
    basis: str
    predicted_seconds: float
    realized_seconds: float | None

    @property
    def outcome(self) -> Outcome:
        if self.realized_seconds is None or self.predicted_seconds <= 0:
            return Outcome.PENDING
        error = (self.realized_seconds - self.predicted_seconds) / self.predicted_seconds
        if abs(error) <= WITHIN:
            return Outcome.WITHIN_BAND
        return Outcome.UNDERESTIMATED if error > 0 else Outcome.OVERESTIMATED

    @property
    def signed_error(self) -> float | None:
        if self.realized_seconds is None or self.predicted_seconds <= 0:
            return None
        return (self.realized_seconds - self.predicted_seconds) / self.predicted_seconds


@dataclass(slots=True)
class BasisReport:
    basis: str
    measured: int = 0
    pending: int = 0
    within_band: int = 0
    overestimated: int = 0
    underestimated: int = 0
    median_signed_error: float | None = None
    kinds: dict[str, int] = field(default_factory=dict)

    @property
    def calibration(self) -> float | None:
        """Share of measured predictions inside the ±25% band, or None if unmeasured.

        None rather than 0.0 on an empty sample: "we have not checked" and "we checked
        and were wrong" are different claims, and reporting them the same way is how a
        dashboard becomes decoration.
        """
        if self.measured == 0:
            return None
        return self.within_band / self.measured


def collect(conn: psycopg.Connection, *, repo_id: int | None = None) -> list[Observation]:
    where = "WHERE f.est_seconds_saved_per_run IS NOT NULL"
    params: list[object] = []
    if repo_id is not None:
        where += " AND f.repo_id = %s"
        params.append(repo_id)

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"""
            SELECT f.id::text AS id, r.owner || '/' || r.name AS repo, f.kind,
                   coalesce(f.savings_basis, 'unknown') AS basis,
                   f.est_seconds_saved_per_run AS predicted,
                   f.realized_seconds_per_run AS realized
            FROM finding f JOIN repo r ON r.id = f.repo_id
            {where}
            """,
            params,
        )
        return [
            Observation(
                finding_id=row["id"], repo=row["repo"], kind=row["kind"],
                basis=row["basis"],
                predicted_seconds=float(row["predicted"]),
                realized_seconds=(
                    float(row["realized"]) if row["realized"] is not None else None
                ),
            )
            for row in cur.fetchall()
        ]


def report(observations: list[Observation]) -> dict[str, BasisReport]:
    """Group by savings basis. Replay and projection are never combined."""
    out: dict[str, BasisReport] = {}
    errors: dict[str, list[float]] = {}

    for obs in observations:
        rep = out.setdefault(obs.basis, BasisReport(basis=obs.basis))
        rep.kinds[obs.kind] = rep.kinds.get(obs.kind, 0) + 1
        outcome = obs.outcome
        if outcome is Outcome.PENDING:
            rep.pending += 1
            continue
        rep.measured += 1
        if outcome is Outcome.WITHIN_BAND:
            rep.within_band += 1
        elif outcome is Outcome.OVERESTIMATED:
            rep.overestimated += 1
        else:
            rep.underestimated += 1
        err = obs.signed_error
        if err is not None:
            errors.setdefault(obs.basis, []).append(err)

    for basis, errs in errors.items():
        if errs:
            out[basis].median_signed_error = statistics.median(errs)
    return out


def record_realized(
    conn: psycopg.Connection, finding_id: str, realized_seconds: float
) -> None:
    """Write the ground truth for one finding.

    Called after a fix has been applied and a comparable window of runs observed. This
    is the only writer of `realized_*`, so a value here always means a real measurement
    took place rather than an estimate being promoted.
    """
    conn.execute(
        "UPDATE finding SET realized_seconds_per_run = %s, realized_at = now()"
        " WHERE id = %s",
        (realized_seconds, finding_id),
    )
    conn.commit()
