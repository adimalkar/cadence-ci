"""Corpus-wide audit sweep — the seed of the Phase 1 eval harness.

Runs the audit unattended across every corpus repo and records, per repo, what was found
and how much of the pipeline we could actually measure. Two Phase 1 ship criteria are
answered from its output, and the same shape feeds the calibration dashboard later.

Workflow files are cached to disk so a re-run (or report generation) does not re-fetch
~15 files per repo against the rate-limit budget.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import structlog

from cadence.audit import build_context, run_audit, summarize_pipeline
from cadence.db import connect
from cadence.providers.base import CIProvider

log = structlog.get_logger(__name__)


@dataclass
class RepoResult:
    repo: str
    ok: bool
    error: str | None = None
    runs: int = 0
    is_private: bool = False
    workflow: str | None = None
    coverage: float = 0.0
    wall_seconds: float = 0.0
    critical_path_seconds: float | None = None
    floor_seconds: float = 0.0
    queue_bound: bool = False
    findings: int = 0
    replay_seconds_per_run: float = 0.0
    projection_low_per_run: float = 0.0
    kinds: list[str] = field(default_factory=list)

    @property
    def recoverable_fraction(self) -> float:
        """Replay-measured recoverable share of median wall clock.

        Replay only. Folding projection in here would inflate the number that the
        premise-check kill criterion is read against.
        """
        if self.wall_seconds <= 0:
            return 0.0
        return self.replay_seconds_per_run / self.wall_seconds


async def sweep(
    provider: CIProvider,
    repos: list[tuple[str, str]],
    *,
    cache_dir: Path,
    window_days: int = 90,
    limit_runs: int = 200,
) -> list[RepoResult]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    out: list[RepoResult] = []

    for owner, name in repos:
        slug = f"{owner}/{name}"
        cache = cache_dir / f"{owner}__{name}.json"
        try:
            repo = await provider.get_repo(owner, name)
            if cache.exists():
                files = json.loads(cache.read_text())
            else:
                files = await provider.fetch_workflow_files(repo)
                cache.write_text(json.dumps(files))
        except Exception as exc:  # one unreachable repo must not end the sweep
            out.append(RepoResult(repo=slug, ok=False, error=str(exc)[:200]))
            log.warning("sweep.fetch_failed", repo=slug, error=str(exc))
            continue

        if not files:
            out.append(RepoResult(repo=slug, ok=False, error="no workflow files"))
            continue

        try:
            with connect() as conn:
                ctx = build_context(
                    conn, repo.id, files, window_days=window_days, limit_runs=limit_runs
                )
                if not ctx.runs:
                    out.append(RepoResult(repo=slug, ok=False, error="no ingested runs"))
                    continue
                summary = summarize_pipeline(ctx)
                result = run_audit(conn, ctx, commit_sha="sweep", persist=False)
        except Exception as exc:
            out.append(RepoResult(repo=slug, ok=False, error=str(exc)[:200]))
            log.warning("sweep.audit_failed", repo=slug, error=str(exc))
            continue

        drafts = result["drafts"]
        replay = sum(
            d.savings.seconds_per_run
            for d in drafts
            if d.savings and d.savings.basis.is_replay
        )
        projection = sum(
            d.savings.low for d in drafts if d.savings and not d.savings.basis.is_replay
        )

        out.append(
            RepoResult(
                repo=slug, ok=True, runs=len(ctx.runs), is_private=ctx.is_private,
                workflow=(summary or {}).get("workflow"),
                coverage=(summary or {}).get("coverage", 0.0),
                wall_seconds=(summary or {}).get("wall_seconds", 0.0),
                critical_path_seconds=(summary or {}).get("critical_path_seconds"),
                floor_seconds=(summary or {}).get("floor_seconds", 0.0),
                queue_bound=(summary or {}).get("queue_bound", False),
                findings=len(drafts),
                replay_seconds_per_run=replay,
                projection_low_per_run=projection,
                kinds=sorted({d.kind for d in drafts}),
            )
        )
        log.info("sweep.repo_done", repo=slug, findings=len(drafts))

    return out


def write_report(results: list[RepoResult], path: Path) -> None:
    path.write_text(json.dumps([asdict(r) for r in results], indent=2))
