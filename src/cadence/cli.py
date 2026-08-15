from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.table import Table

from cadence.config import settings
from cadence.corpus import CORPUS
from cadence.db import apply_migrations, connect
from cadence.ingest import ingest_repo
from cadence.logstore import LocalLogStore
from cadence.providers import GitHubProvider
from cadence.queue import enqueue
from cadence.worker import run_worker

app = typer.Typer(no_args_is_help=True, help="Evidence-grounded CI intelligence.")
db_app = typer.Typer(no_args_is_help=True, help="Database management.")
worker_app = typer.Typer(no_args_is_help=True, help="The ingest queue's consumer side.")
queue_app = typer.Typer(no_args_is_help=True, help="Inspect the ingest queue.")
corpus_app = typer.Typer(no_args_is_help=True, help="The no-install evaluation corpus.")
webhook_app = typer.Typer(no_args_is_help=True, help="The webhook receiver.")
app.add_typer(db_app, name="db")
app.add_typer(worker_app, name="worker")
app.add_typer(queue_app, name="queue")
app.add_typer(corpus_app, name="corpus")
app.add_typer(webhook_app, name="webhook")

console = Console()


def _require_token() -> str:
    if not settings.github_token:
        console.print(
            "[red]No GitHub token.[/red] Set CADENCE_GITHUB_TOKEN in .env — "
            "the gh CLI's works: [cyan]export CADENCE_GITHUB_TOKEN=$(gh auth token)[/cyan]"
        )
        raise typer.Exit(1)
    return settings.github_token


@db_app.command("init")
def db_init() -> None:
    """Apply pending migrations."""
    applied = apply_migrations()
    if applied:
        for name in applied:
            console.print(f"[green]applied[/green] {name}")
    else:
        console.print("[dim]schema up to date[/dim]")


@app.command()
def ingest(
    repo: str = typer.Argument(..., help="owner/name"),
    limit: int = typer.Option(100, help="How many recent runs to pull."),
    no_jobs: bool = typer.Option(False, "--no-jobs", help="Run metadata only, skip step timings."),
) -> None:
    """Ingest a repo's run history. Read-only; no App install required."""
    token = _require_token()
    try:
        owner, name = repo.split("/", 1)
    except ValueError:
        console.print("[red]repo must be owner/name[/red]")
        raise typer.Exit(1) from None

    async def _run() -> None:
        provider = GitHubProvider(token)
        try:
            with connect() as conn, console.status(f"ingesting {repo}…"):
                stats = await ingest_repo(
                    provider, conn, owner, name, limit=limit, fetch_jobs=not no_jobs
                )
            if stats.not_modified:
                console.print(f"[dim]{repo}: unchanged since last poll (304)[/dim]")
            else:
                console.print(f"[green]{repo}[/green]: {stats}")
        finally:
            await provider.aclose()

    asyncio.run(_run())


@app.command()
def stats(repo: str = typer.Argument(..., help="owner/name")) -> None:
    """What we hold for a repo, and whether the timings hang together."""
    try:
        owner, name = repo.split("/", 1)
    except ValueError:
        console.print("[red]repo must be owner/name[/red]")
        raise typer.Exit(1) from None

    with connect() as conn:
        row = conn.execute(
            "SELECT id, is_private, first_ingested_at, last_polled_at"
            " FROM repo WHERE owner = %s AND name = %s",
            (owner, name),
        ).fetchone()
        if not row:
            console.print(f"[yellow]{repo} not ingested yet[/yellow]")
            raise typer.Exit(1)

        repo_id = row["id"]
        counts = conn.execute(
            """
            SELECT
              (SELECT count(*) FROM run WHERE repo_id = %(r)s) AS runs,
              (SELECT count(*) FROM job WHERE repo_id = %(r)s) AS jobs,
              (SELECT count(*) FROM step s JOIN job j ON j.id = s.job_id
                WHERE j.repo_id = %(r)s) AS steps,
              (SELECT count(*) FROM run WHERE repo_id = %(r)s AND run_attempt > 1) AS reruns,
              (SELECT count(*) FROM run
                WHERE repo_id = %(r)s AND conclusion = 'failure') AS failures,
              (SELECT min(created_at) FROM run WHERE repo_id = %(r)s) AS oldest,
              (SELECT max(created_at) FROM run WHERE repo_id = %(r)s) AS newest
            """,
            {"r": repo_id},
        ).fetchone()

        table = Table(title=f"{repo}  ({'private' if row['is_private'] else 'public'})")
        table.add_column("metric", style="cyan")
        table.add_column("value", justify="right")
        table.add_row("runs", f"{counts['runs']:,}")
        table.add_row("jobs", f"{counts['jobs']:,}")
        table.add_row("steps", f"{counts['steps']:,}")
        table.add_row("failures", f"{counts['failures']:,}")

        # The rerun rate is the leading indicator for Phase 3: published research puts it
        # near 3.2% of builds, of which ~68% are genuinely flaky. If the corpus comes in
        # far below that, the flaky classifier will not have enough gold labels to train
        # and ingest breadth has to increase before week 14.
        rerun_pct = (counts["reruns"] / counts["runs"] * 100) if counts["runs"] else 0.0
        table.add_row("reruns (gold-label source)", f"{counts['reruns']:,} ({rerun_pct:.1f}%)")

        if counts["oldest"]:
            span = counts["newest"] - counts["oldest"]
            table.add_row("history span", f"{span.days} days")
        console.print(table)

        # Phase 0 ship criterion, stated correctly.
        #
        # A job is NOT just its steps. Measured against real data, it decomposes as:
        #
        #   job.started_at ──[provisioning]── step1..stepN ──[cleanup]── job.completed_at
        #
        # Provisioning is runner allocation — real time, billed and felt, but not step
        # time and not optimizable by editing steps. Comparing step sums to *job* span
        # therefore measures provisioning overhead, not ingest fidelity.
        #
        # Fidelity is whether steps tile their own span. That is what the simulator
        # replays, and it is the number that must hold.
        fidelity = conn.execute(
            """
            SELECT count(*) AS n,
                   avg(err) AS mean_err,
                   count(*) FILTER (WHERE err > 0.02) AS over_2pct
            FROM (
              SELECT abs(
                       sum(extract(epoch FROM (s.completed_at - s.started_at)))
                       - extract(epoch FROM (max(s.completed_at) - min(s.started_at)))
                     ) / nullif(
                       extract(epoch FROM (max(s.completed_at) - min(s.started_at))), 0
                     ) AS err
              FROM job j JOIN step s ON s.job_id = j.id
              WHERE j.repo_id = %s
                AND j.started_at IS NOT NULL AND j.completed_at IS NOT NULL
                AND s.started_at IS NOT NULL AND s.completed_at IS NOT NULL
              GROUP BY j.id
              HAVING extract(epoch FROM (max(s.completed_at) - min(s.started_at))) > 5
            ) t WHERE err IS NOT NULL
            """,
            (repo_id,),
        ).fetchone()

        if fidelity and fidelity["n"]:
            pct = fidelity["over_2pct"] / fidelity["n"] * 100
            ok = pct < 5
            console.print(
                f"\n[bold]step-timing fidelity[/bold] ({fidelity['n']:,} jobs ≥5s): "
                f"mean error [{'green' if ok else 'red'}]{fidelity['mean_err'] * 100:.2f}%"
                f"[/{'green' if ok else 'red'}], "
                f"{fidelity['over_2pct']} ({pct:.1f}%) exceed 2%  "
                f"{'[green]✓ ship criterion met[/green]' if ok else '[red]✗ FAILING[/red]'}"
            )

        # Per-job provisioning overhead. This is a fixed tax on every job, which means
        # adding a parallel job is never free -- and the universal CI advice
        # ("parallelize more") has a floor we can actually quantify. No competing tool
        # separates this from execution time.
        prov = conn.execute(
            """
            SELECT count(*) AS n,
                   avg(lead) AS mean_lead,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY lead) AS p50_lead,
                   percentile_cont(0.9) WITHIN GROUP (ORDER BY lead) AS p90_lead,
                   avg(lead / nullif(job_secs, 0)) AS frac
            FROM (
              SELECT extract(epoch FROM (min(s.started_at) - j.started_at)) AS lead,
                     extract(epoch FROM (j.completed_at - j.started_at)) AS job_secs
              FROM job j JOIN step s ON s.job_id = j.id
              WHERE j.repo_id = %s
                AND j.started_at IS NOT NULL AND j.completed_at IS NOT NULL
                AND s.started_at IS NOT NULL
              GROUP BY j.id, j.started_at, j.completed_at
              HAVING extract(epoch FROM (j.completed_at - j.started_at)) > 5
            ) t
            -- negative lead == clock skew between the runner and GitHub; drop, don't model
            WHERE lead >= 0
            """,
            (repo_id,),
        ).fetchone()

        if prov and prov["n"]:
            # psycopg returns numeric aggregates as Decimal; normalize before arithmetic.
            mean_lead = float(prov["mean_lead"])
            p50_lead, p90_lead = float(prov["p50_lead"]), float(prov["p90_lead"])
            frac = float(prov["frac"])
            jobs_per_run = counts["jobs"] / counts["runs"] if counts["runs"] else 0
            per_run = mean_lead * jobs_per_run
            console.print(
                f"[bold]runner provisioning[/bold]: "
                f"p50 {p50_lead:.0f}s · p90 {p90_lead:.0f}s · "
                f"[cyan]{frac * 100:.1f}%[/cyan] of job wall-clock\n"
                f"  [dim]{jobs_per_run:.1f} jobs/run ⇒ ~{per_run / 60:.1f} min/run of "
                f"fixed overhead. Each added parallel job costs "
                f"~{mean_lead:.0f}s before it runs anything.[/dim]"
            )


@app.command()
def logs(
    repo: str = typer.Argument(..., help="owner/name"),
    limit: int = typer.Option(200, help="Max jobs to queue for log fetch."),
    failures_only: bool = typer.Option(
        False, "--failures-only", help="Only queue jobs that failed."
    ),
) -> None:
    """Queue job logs for fetch-and-store. Run `cadence worker run --until-empty` after
    -- this only enqueues; a worker does the downloading, since log fetch is the
    single biggest consumer of rate-limit budget and should never happen silently."""
    try:
        owner, name = repo.split("/", 1)
    except ValueError:
        console.print("[red]repo must be owner/name[/red]")
        raise typer.Exit(1) from None

    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM repo WHERE owner = %s AND name = %s", (owner, name)
        ).fetchone()
        if not row:
            console.print(f"[yellow]{repo} not ingested yet[/yellow]")
            raise typer.Exit(1)
        repo_id = row["id"]

        cond = "AND j.conclusion = 'failure'" if failures_only else ""
        rows = conn.execute(
            f"""
            SELECT j.id FROM job j
            LEFT JOIN log_chunk lc ON lc.job_id = j.id
            WHERE j.repo_id = %s AND lc.job_id IS NULL {cond}
            ORDER BY j.completed_at DESC NULLS LAST
            LIMIT %s
            """,
            (repo_id, limit),
        ).fetchall()

        for r in rows:
            enqueue(conn, "fetch_log", {"repo_id": repo_id, "job_id": r["id"]})
        conn.commit()

    console.print(f"[green]queued[/green] {len(rows)} log fetches for {repo}")


@corpus_app.command("seed")
def corpus_seed(
    limit: int = typer.Option(40, help="Runs to pull per repo on the first poll."),
) -> None:
    """Queue a poll_repo job for every repo in the no-install corpus (docs/HELDOUT.md
    repos are never in this list)."""
    with connect() as conn:
        for owner, name in CORPUS:
            enqueue(conn, "poll_repo", {"owner": owner, "name": name, "limit": limit})
        conn.commit()
    console.print(f"[green]queued[/green] {len(CORPUS)} repos")


@queue_app.command("status")
def queue_status() -> None:
    """What the ingest queue is holding, by kind and status."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT kind, status, count(*) AS n FROM ingest_job"
            " GROUP BY kind, status ORDER BY kind, status"
        ).fetchall()
    table = Table(title="ingest queue")
    table.add_column("kind", style="cyan")
    table.add_column("status")
    table.add_column("n", justify="right")
    for r in rows:
        table.add_row(r["kind"], r["status"], f"{r['n']:,}")
    console.print(table)


@worker_app.command("run")
def worker_run(
    concurrency: int = typer.Option(4, help="Concurrent claim-loops."),
    until_empty: bool = typer.Option(
        False, "--until-empty", help="Drain the current backlog, then exit."
    ),
    max_idle: int = typer.Option(
        0, help="Exit after N idle polls found nothing claimable. 0 = run forever."
    ),
) -> None:
    """Drain the ingest queue. Without a flag, runs forever -- this is the process a
    real deployment keeps alive so corpus repos keep re-polling every 30 minutes."""
    token = _require_token()

    async def _run() -> None:
        provider = GitHubProvider(token)
        log_store = LocalLogStore(settings.log_store)
        try:
            await run_worker(
                provider,
                log_store,
                concurrency=concurrency,
                until_empty=until_empty,
                max_idle_iterations=max_idle or None,
            )
        finally:
            await provider.aclose()

    asyncio.run(_run())


@webhook_app.command("serve")
def webhook_serve(
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(8787),
) -> None:
    """Run the webhook receiver. Dev/local only -- a real deployment sits this behind
    a reverse proxy with TLS, since GitHub requires HTTPS for webhook delivery."""
    import uvicorn

    from cadence.webhook import app as webhook_asgi_app

    uvicorn.run(webhook_asgi_app, host=host, port=port)


if __name__ == "__main__":
    app()
