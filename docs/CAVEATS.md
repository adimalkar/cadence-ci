# Caveats, Known Gaps and Open Findings

Every implementation session throws off things that are not the thing being built: a bug
found in passing, a compromise taken knowingly, a check that only half-works, a decision
deferred. They are obvious on the day and gone a fortnight later. This file is where they
go.

**Maintenance rule: append here at the end of every implementation session, before moving
on.** An entry is cheap to write and expensive to rediscover.

**Conventions**

- Entries are never deleted. When one is resolved it moves to [Resolved](#resolved) with a
  date and the commit that closed it, so the reasoning survives.
- Every entry states **what**, **why it matters**, and **what would close it** — an entry
  nobody can act on is a note, not a caveat.
- *Pre-existing* marks items that came from the project's own docs rather than from an
  implementation session; they are repeated here so this file is the single list to read.

---

## Summary

| # | Item | Severity | Status |
|---|---|---|---|
| 1 | Ingest has been stopped since 2026-08-24 | **Critical** | ✅ Resolved 2026-08-28 |
| 2 | Rate card is stale — self-hosted charge is live | **High** | ✅ Resolved 2026-08-26 |
| 3 | Two cost fallbacks disagree for the same runner | **High** | ✅ Resolved 2026-08-26 |
| 24 | Self-hosted pricing is unvalidated — corpus is 100% public | Medium | Open |
| 25 | Worker deployment is laptop-bound and unproven over weeks | Medium | Open |
| 26 | A space in the project path breaks systemd unit settings | Low | Documented |
| 27 | Worker shares the user's personal token and its rate limit | **High** | Open |
| 28 | Config snapshots are captured only by `cadence audit`, never by ingest | Medium | Open |
| 29 | `matrix_legs_never_independent` measured but unbuilt (`job_billing_rounding` shipped) | Medium | Partly resolved |
| 30 | Dollars-only findings cannot be ranked — `Savings` implies wall-clock | Medium | Open |
| 31 | `recoverable_fraction` reported up to 5,132% — partial re-runs inflate cancellation replay | **High** | ✅ Largely fixed 2026-09-03 |
| 35 | Multi-attempt runs were double-counting jobs in matrix and billing analysis | Medium | ✅ Fixed 2026-09-03 |
| 36 | F8 and F6 specified but not started — the only candidates that can move Phase 1's criterion | Medium | Open |
| 37 | Finding suppression has four schema columns and no writer — Phase 2's anti-spam rule 3 is unimplementable | **High** | Open |
| 38 | Phase 6 overstated the novelty of verified liveness | Low | ✅ Corrected 2026-09-03 |
| 32 | Worker crashes if Postgres is not up at boot, then hangs on dead connections | **High** | ✅ Resolved 2026-09-03 |
| 33 | ~~Queue has no claim lease~~ — **wrong, the lease exists**; the worker hangs instead | **High** | ✅ Corrected + fixed |
| 34 | Four large-backfill jobs hang the worker deterministically after exhausting the rate limit | **High** | Mitigated by 33's fix; cause is 27 |
| 4 | `CIProvider` protocol missing `fetch_workflow_files` | Medium *(suspected)* | Open |
| 5 | `Run.created_at` non-optional vs nullable payload | Medium *(suspected)* | Open |
| 6 | Worker deployment shape undecided | **Blocker** | ✅ Resolved 2026-08-28 |
| 7 | PyPI Trusted Publishing not configured | Medium | Awaiting action |
| 8 | mypy baseline exempts 8 modules | Medium | Accepted, burn down |
| 9 | `ruff format` not adopted | Low | Deliberate |
| 10 | Coverage 65%, `worker.py` at 31% | Medium | Accepted |
| 11 | Admins bypass required checks | Low | Deliberate |
| 12 | `dependency-review` never runs on direct pushes | Medium | Structural |
| 13 | Shuffled test order can surface latent bugs sporadically | Low | Deliberate |
| 14 | Service container images not digest-pinned | Low | Open |
| 15 | No fuzzing of untrusted-input parsers | Medium | Open |
| 16 | Workflow config is never persisted | **High** | ✅ Resolved 2026-08-29 |
| 17 | Reusable-workflow mapping 18–100% | **High** | Open *(pre-existing)* |
| 18 | Phase 1 ship criterion 2 fails | **High** | Open *(pre-existing)* |
| 19 | Report never checked with a screen reader | Medium | Open *(pre-existing)* |
| 20 | Reddit is unreachable directly | Info | Worked around |
| 21 | `setup-uv` publishes no floating major tags past v7 | Info | Worked around |
| 22 | Phase 3 demand signal is weaker than the plan assumes | **High** | Open question |
| 23 | Stacked-PR detection needs two mandatory guards | Medium | Not yet built |

---

## Wrong right now

### 1. ~~Ingest has been stopped since 2026-08-24~~ · RESOLVED 2026-08-28 · Critical

**What.** No worker process, no systemd unit, no container. Last `run.ingested_at` is
2026-08-24 02:00. 214 jobs are queued: 132 `poll_repo` overdue 3 days, 57 `webhook_event`
and 25 `fetch_log` overdue **11 days**.

**Why it matters.** The only irreversible deadline in the plan. GitHub retains logs 90
days, so this is permanently lost history, not deferred work. It also blocks the diagnosed
fix for item 18 — Phase 1's failing criterion is an ingest-depth problem, so building more
detectors while ingest is stopped produces rules with no sample to fire on.

**Closed by** a systemd *user* unit (`deploy/`), installed and running. Ingest resumed
immediately: staleness went from 2d 23h to 14 seconds, ~1,000 runs ingested in the first
five minutes, `fetch_log` and `webhook_event` queues fully drained, `poll_repo` draining,
**zero failures**. `loginctl enable-linger` makes it survive logout and start at boot.

**Correction to the original measurement above:** the "214 queued, some overdue 11 days"
figure counted `done` rows as well as `pending` — `ingest_job` retains completed jobs. The
genuine pending backlog was smaller. The staleness figure was accurate; the queue figure
was not. See item 25 for what this still does not prove.

### 24. Self-hosted pricing is correct but unvalidated · Medium

**What.** Item 2's fix is covered by 15 unit tests, but **all 55 corpus repos are public**,
and public repos pay nothing on self-hosted runners either way. So no corpus figure changes
and no end-to-end evidence exists that the new rate resolves correctly on real ingested
data.

**Why it matters.** The commercial case for item 2 rests on private repos on self-hosted
runners — precisely the population the corpus does not contain. The fix is right by
construction and by unit test, not by observation.

**Closes when.** Either a private repo enters the corpus, or an `evalsweep` run is done
against a synthetic private-repo fixture that exercises the self-hosted path end to end.

---

### 2. ~~The rate card is stale~~ · RESOLVED 2026-08-26 · High

**What.** [`cost.py`](../src/cadence/cost.py) reasons the self-hosted per-minute charge is
"a shelved self-hosted charge may yet return." It is not shelved — it took effect
**2026-03-01**. GitHub applies **$0.002/min** to all workflows including self-hosted;
hosted rates fell up to 39% on 2026-01-01 (which the card does reflect); public repos stay
free; private-repo self-hosted minutes consume the free quota.

**Why it matters.** `usd_per_minute` returns `0.0` for unknown labels, so every
self-hosted job on a private repo is under-billed. The corpus contains exactly those
labels — `depot-ubuntu-24.04-*`, `depot-ubuntu-22.04-*`, `ubuntu-latest-8core`,
`ubuntu-slim`, `codspeed-macro`. The audience most likely to buy a minute-reduction tool
moved to self-hosted to escape per-minute billing; they now pay again and we quote them
**$0**.

**Closed by** `rate_card` version **20260301** (migration `004`): hosted rows copied
unchanged, plus a `__self_hosted__` sentinel at $0.002 with `free_on_public`. Version 2026
is left intact, so the one existing finding stamped with it still reproduces its original
figure. See item 24 for what this fix does *not* prove.

### 3. ~~Two cost fallbacks disagree about the same runner~~ · RESOLVED 2026-08-26 · High

**What.** `usd_per_minute` returns `0.0` for an unknown label; `hypothetical_dollars_per_month`
uses `rates.get(label, 0.006)` — the hosted-Linux rate, 3× the real $0.002 charge.

**Why it matters.** One runner, two prices, and both can appear in the same report. The
`0.0` is documented as a deliberate "never fabricate" rule; the `0.006` silently violates
it.

**Closed by** routing `hypothetical_dollars_per_month` through `usd_per_minute` with
`is_private=True`, deleting the hard-coded 0.006. `tests/test_cost.py` asserts the two
paths agree for hosted, larger and self-hosted runners.

---

## Suspected bugs, surfaced by tooling, not yet verified

Both came out of the mypy baseline (item 8). Neither has been confirmed against live data
— they are flagged rather than fixed because both need domain judgement.

### 4. `CIProvider` protocol is missing `fetch_workflow_files` · Medium

`evalsweep.py:75` calls it; the protocol does not declare it. Works at runtime because
`GitHubProvider` implements it, so the protocol is out of sync with its only implementation
— which matters the moment a second provider exists.

### 5. `Run.created_at` is non-optional but the payload can be null · Medium

`providers/github.py:203` and `:368` pass `datetime | None` into a field typed `datetime`.
If GitHub ever returns null, this is a silent data-fidelity fault of exactly the kind the
Phase 0 audit found four of.

---

## Blocked on a decision or an external step

### 6. ~~Worker deployment shape undecided~~ · RESOLVED 2026-08-28 · Blocker

Decided: **systemd user unit**, on the reasoning that it runs today and a container is a
clean follow-up when this leaves the laptop. `deploy/cadence-worker.service` is a template
with placeholders; `deploy/install.sh` substitutes paths, writes a 0600 credential file
outside the repo, enables linger, and starts the unit. Re-runnable after a code change or
token rotation.

### 7. PyPI Trusted Publishing not configured · Medium

[`release.yml`](../.github/workflows/release.yml) is dormant until a `v*` tag and will fail
on the first one until a publisher is registered at
<https://pypi.org/manage/account/publishing/> — project `cadence`, owner `adimalkar`, repo
`cadence-ci`, workflow `release.yml`, environment `pypi`. Deliberately stores no API token.

---

## Deliberate compromises

Recorded so they are not mistaken for oversights, and so the reasoning can be re-examined
rather than re-derived.

### 8. mypy exempts 8 modules · Medium

Introducing mypy surfaced 25 errors across `audit`, `dag`, `evalsweep`, `findings`,
`ingest`, `models`, `providers.github`, `queue`. They are listed in `pyproject.toml` with
`ignore_errors` as a **ratchet**: everything else is gated, so new code lands clean. The
list is meant to shrink and nothing should be added to it. Items 4 and 5 came from this
set.

### 9. `ruff format` is not adopted · Low

Would reformat **30 of 46 files**. ruff is configured as a linter only. Adopting the
formatter is a real decision with a blame-flattening diff attached; it is not part of
"turn CI on" and was left alone.

### 10. Coverage is 65%, and `worker.py` is 31% · Medium

CI enforces a **60%** floor — set below current to catch a collapse without inviting the
gaming that aggressive coverage targets produce. `worker.py` being lowest is the notable
part, since it is the component item 1 is about to make load-bearing.

### 11. Admins bypass required checks · Low

`enforce_admins: false`, chosen deliberately to keep a solo direct-push workflow. The
consequence is real and visible: pushes report `Bypassed rule violations`. CI is binding
for pull requests, advisory for direct pushes.

### 12. `dependency-review` never runs on direct pushes · Medium

The action diffs a PR against its base, so it cannot run on push. Combined with item 11,
a dependency with a known advisory can reach `main` unreviewed. `pip-audit` catches it on
the next run, so this is a delay rather than a hole — but the delay is unbounded if nobody
pushes again.

### 13. Shuffled test order can surface latent bugs sporadically · Low

`pytest-randomly` uses a fresh seed each run, so an order-dependent test fails
intermittently rather than never. That is the point — it already caught one real bug — but
it means a red CI run may not reproduce on retry. The seed is printed; reproduce with
`--randomly-seed=N`.

---

## Known gaps

### 14. Service container images are not digest-pinned · Low

zizmor reports 3 `unpinned-images`: the `postgres:16` service containers. Tags are mutable,
the same argument that got every action SHA-pinned. Lower priority — a test-only Postgres
is a much smaller blast radius than an action with repo write access.

### 15. No fuzzing of untrusted-input parsers · Medium

The workflow YAML parser and the log normalizer both consume input controlled by anyone who
can open a PR against an ingested repo. They are the obvious first fuzz targets. Also
listed as a known gap in [`SECURITY.md`](../SECURITY.md).

### 16. ~~Workflow config is never persisted~~ · RESOLVED 2026-08-29 · High

[`cli.py:381`](../src/cadence/cli.py#L381) fetches workflow files live at audit time; there
is no config table. Consequences: no config history, so "the workflow changed here and
waste started" is unanswerable; **Phase 2's round-trip ship criterion is not reproducible
as written**, because re-fetching from HEAD lets the corpus shift under the test; and Phase
3 blame loses a strong feature. **Closed by** migration `005` and `src/cadence/configstore.py` (#3). Content-addressed
like `log_chunk`; an edit inserts a new `workflow_snapshot` row rather than mutating, so a
path's history is its rows ordered by `first_seen`. Deliberately **not** keyed by commit
sha — anchoring to a commit needs either an extra request per capture to resolve HEAD or a
change to which ref the audit reads, and with it the analysis results. Verified end to end
against `astral-sh/ruff`: 20 files, 174 kB, through the production path. See item 28 for
what it still does not do.

### 17. Reusable workflows map at 18–100% · High · *pre-existing*

`jobs.x.uses: ./.github/workflows/_build.yml` renames runtime jobs to `x / <inner>`,
matching nothing in the calling file. Below 80% coverage the critical path is withheld
rather than shown. Named in [`ROADMAP.md`](ROADMAP.md) as the highest-value Phase 1 task.

### 18. Phase 1 ship criterion 2 fails · High · *pre-existing*

Median 1 finding against a target of 3; median 0.0% recoverable against 10%. Diagnosed as
(a) ingest depth too shallow — median 4 runs per workflow, only 39 of 544 streams reach
`MIN_RUNS = 20` — and (b) only 4 of ~14 catalog rules built. Cause (a) is item 1.

### 19. The report has never been checked with a screen reader · Medium · *pre-existing*

It renders at 375px and every number is real text, but no assistive-technology pass has
happened. Claiming accessibility without testing it is the kind of unverified claim the
rest of this project avoids.

---

### 25. The worker is laptop-bound and unproven over weeks · Medium

**What.** The unit runs on this machine only. Linger survives logout and reboot, but not a
powered-off laptop, and the Postgres it writes to is local.

**Why it matters.** ROADMAP's Phase 0 note is explicit that "the mechanism firing correctly
across one interval boundary is not the same as a deployment running for weeks unattended."
That is still true — this closes the *stopped* problem, not the *durable* one. History
accrues only while the machine is on.

**Closes when.** The worker runs somewhere always-on. The container option from item 6 is
the intended path, and `deploy/` is deliberately structured so that is additive.

### 26. A space in the project path breaks systemd unit settings · Low

**What.** The directory is `Cadence System`. systemd parses `ReadWritePaths=` and
`ExecStart=` as whitespace-separated lists, so unquoted they kept only
`/mnt/.../Cadence` and the unit died at `226/NAMESPACE`. `WorkingDirectory=` is the
opposite — a single path that must **not** be quoted, since it treats the quotes as part of
the value and rejects it as non-absolute.

**Why it matters.** Two settings in one file with opposite quoting rules, failing in
different ways. Any new path-valued setting added to the unit needs this checked.
`systemd-analyze --user verify <unit>` catches it before a start attempt.

### 27. The worker runs on the user's personal token, and shares its rate limit · High

**What.** `deploy/install.sh` seeds `CADENCE_GITHUB_TOKEN` from `gh auth token`, so the
ingest worker authenticates as the user. Observed 2026-08-28: the worker deep-backfilling
51 corpus repos plus interactive `gh` use exhausted the shared **5,000 requests/hour**
budget to zero.

**Why it matters, in two separate ways.**

1. **Contention.** Ingest and interactive work compete for one quota. Whichever runs first
   starves the other, and neither is aware of the other. The worker degrades correctly --
   it backs off and retries, and it recorded zero failures through the exhaustion -- but it
   simply stops making progress, which is the thing item 1 exists to prevent.
2. **Scope.** A `gh` CLI token carries `repo`, `workflow`, `gist` and `read:org`. The worker
   needs **read access to public repositories and nothing else**. A credential sitting on
   disk in a long-lived service should carry the least authority that does the job, and this
   one carries enough to push to any repository the user owns.

**Closes when.** The worker gets its own credential: a fine-grained PAT scoped to
read-only Actions and metadata on the corpus repos, or -- better, and required for Phase 2
anyway -- a GitHub App installation token, which has a separate 5,000/hour budget per
installation and can be scoped per repository. `install.sh` should stop defaulting to
`gh auth token`, or at minimum warn that it is doing so.

**Interim mitigation.** None applied. Worth knowing that a run of heavy `gh` use will
stall ingest until the hour rolls over.

### 28. Config snapshots are captured only by `cadence audit` · Medium

**What.** `store_snapshot` is wired into the audit path, where the workflow files are
already in memory and cost nothing extra. The ingest worker does **not** capture config, so
a repo that is polled continuously but never audited accrues no config history at all.

**Why it matters.** The three things item 16 set out to enable only partly arrived.
Reproducible re-analysis works, because an audited repo has its bytes stored. But "the
workflow changed here and waste started" needs a *time series*, and the corpus is polled
every 30 minutes while being audited approximately never — so for most repos there will be
exactly one snapshot, and `history()` will return one row forever.

**Why it was built this way.** Capturing during ingest means one extra API request per repo
per poll, against the rate limit item 27 documents as already exhausted. That trade is
worth making deliberately rather than by default.

**Closes when.** Either the worker captures config on a cadence of its own (daily rather
than per-poll would be enough to build history cheaply), or ingest gains a conditional
request — the contents API supports ETags, so an unchanged directory costs a 304 rather
than a full read.

### 29. Two measured rules are specified but not built · Medium

**What.** Two Phase 1 catalog rules were measured against the live corpus on 2026-08-30 and
written up in [`FEATURE_CANDIDATES.md`](FEATURE_CANDIDATES.md), but neither exists in code.

- `job_billing_rounding` — **1,002 hours, 7.0% of all billed minutes** across 114,778 jobs.
  `pallets/flask` loses 67.3%, `react/react` 30.2%.
- `matrix_legs_never_independent` — `Ubuntu` and `Analyze` matrices recorded **zero**
  divergent leg outcomes across 96 runs each, while `build` disagreed in 31 of 298.

**Why it matters.** Phase 1's ship criterion fails at median 1 finding against a target of
3, and the diagnosis is that the rules finding *large* time are unbuilt. `job_billing_rounding`
would fire on nearly every corpus repo with replay-grade evidence. This is the cheapest
known move on the number currently blocking the phase.

**Caveat carried with the first rule:** merging short jobs cuts the bill but reduces
parallelism, so wall-clock feedback can worsen. It is the first rule where hours and dollars
genuinely conflict; the finding must show both and must not fire where merging would extend
the critical path.

**`job_billing_rounding` shipped 2026-08-30** (`detectors/billing.py`, 24 tests). Building
it produced a correction worth keeping: **the rule is silent on the entire corpus**, because
standard runners are free on public repos and all 55 corpus repos are public. It fires on
private repos and larger runners. The earlier claim that it would move Phase 1's criterion
was wrong — that criterion is measured on public repos, so only wall-clock findings can move
it, and this one finds dollars.

**Closes when.** `matrix_legs_never_independent` is implemented with tests, or explicitly
dropped with a reason.

### 30. Dollars-only findings cannot be ranked · Medium

**What.** `job_billing_rounding` emits `savings=None` deliberately.
`Savings.seconds_per_run` feeds the replay total, which the report renders as **wall-clock
hours recovered**. Billed seconds are not wall-clock seconds: merging short jobs recovers
money, recovers no elapsed time, and may cost some.

**Why it matters.** A finding worth $300/month on a private repo shows `—` in the saving
column and cannot rank against findings that save time, because the report orders by time
recovered.

**Closes when.** The model gains a way to express a billed-only saving — a second sort key,
or a flag on `FindingDraft` letting the report render dollars while suppressing a wall-clock
claim. Deliberately not done while shipping the detector: it touches the credibility model
in `PRODUCT.md` §6 and deserves its own decision.

### 31. ~~`recoverable_fraction` mixes billed and wall-clock seconds~~ · LARGELY FIXED 2026-09-03 · High

**What.** `RepoResult.recoverable_fraction` is documented as the "recoverable share of median
wall clock" and computes `replay_seconds_per_run / wall_seconds`. The numerator sums savings
across **parallel jobs** — `no_run_cancellation` recovers billed seconds on every job of every
superseded run, which is why `FindingDraft` carries `parallel_jobs` at all. The denominator is
**elapsed** time for one run. Different units.

**Measured 2026-09-02.** 3 of 49 corpus repos exceed 100%:

| Repo | Reported | Median wall | Replay/run |
|---|---:|---:|---:|
| `sveltejs/kit` | **5,132%** | 704s | 36,131s |
| `tauri-apps/tauri` | 285% | | |
| `pytorch/pytorch` | 253% | | |

`sveltejs/kit` has 100% mapping coverage and two ordinary findings, so this is not a coverage
artifact — 36,131 billed seconds on a 704-second run is roughly 51 jobs running in parallel,
which is entirely plausible and entirely not a share of wall clock.

**Why it matters.** This is the number the **premise kill criterion** is read against — *"<10%
median recoverable wall time at wk 10 → premise wrong, stop before Phase 2."* A metric that can
report 5,132% cannot be trusted to decide that. The median is robust to the outliers (0.9% with
them, 0.7% without), so today's conclusion does not change — but the mean is meaningless
(124.7%), and any single repo's figure may be inflated.

**Same class of error** as the one `job_billing_rounding` was built to avoid: billed seconds are
not wall-clock seconds. That rule guards against it in the detector; this one is in the metric.

**The original diagnosis was wrong.** The numerator is not billed seconds —
`SupersededRun.wasted_seconds` is elapsed overlap, and `evalsweep` sums `seconds_per_run`
without applying `parallel_jobs`. Both sides were already wall-clock.

**The actual cause, traced 2026-09-03.** GitHub's *partial* re-run carries the successful jobs
of the previous attempt forward into the new attempt **with their original timestamps**. So one
attempt can hold jobs days apart: `sveltejs/kit` run `33123664062` has attempt 2 containing a job
started 2026-08-27 next to one completed 2026-08-31. Nothing is wrong with the ingest — that is
what the API reports — but the run's span becomes 3.76 days of mostly nothing, and
`find_superseded_runs` then sees it overlapping every other run in the window.

That single run contributed **323,340 of its repo's 325,025 wasted seconds** — 99.5% — and drove
the reported recoverable share to 5,132%. 163 of 22,340 corpus runs (0.73%) have spans over six
hours, so the population is small and the effect concentrated.

**Fixed** by excluding runs whose span exceeds `MAX_PLAUSIBLE_RUN_SECONDS` (24h) from
cancellation replay, and reporting the exclusion count as evidence rather than dropping it
silently — a run that was not executing continuously cannot have its whole span counted as
cancellable compute. Excluded rather than clamped: a clamp would invent a number for a run we
cannot measure.

Measured effect on the corpus sweep: **mean recoverable 157.2% → 18.1%**.

**Not fully closed.** Three repos still report above 100% (`pytorch/pytorch` 244%,
`tauri-apps/tauri` 193%, `psf/requests` 103%). The median — which is what the kill criterion is
read against — was never affected and remains 0.9%. The residue is a smaller instance of the same
family: a sum-based numerator amortised over a median-based denominator will exceed 1.0 whenever
a workflow's runs overlap heavily. Closing it properly means deciding what "share of wall clock"
should mean when many runs are in flight at once.

### 35. Multi-attempt runs double-counted their jobs · Medium · FIXED 2026-09-03

**What.** `build_context` selected every job for a run regardless of `attempt`, so a re-run's
jobs appeared alongside the original's in one `RunObservation`. Phase 0 ingests earlier attempts
deliberately — a re-run's failing jobs are Phase 3's gold labels — but a run *observation* must
describe one execution.

**Effect.** Matrix-leg analysis saw each leg twice for any re-run, and `job_billing_rounding`
counted the same job's billed minute more than once.

**Fixed** by filtering both job queries on `j.attempt = r.run_attempt`. Note this does **not**
fix item 31 on its own, because GitHub's partial re-run keeps carried-forward jobs inside the
latest attempt with their old timestamps.

### 32. ~~The worker crashes if Postgres is not up at boot, then hangs~~ · RESOLVED 2026-09-03 · High

**What.** Observed 2026-09-01. Postgres was not accepting connections when the user unit started
at boot; the worker exited 1 with `connection to server on socket "/var/run/postgresql/.s.PGSQL.5432"
failed`. systemd restarted it 30s later, it claimed 4 `poll_repo` jobs, and then **logged nothing
for 18h49m** — 21 seconds of CPU across the whole period, sleeping in `poll_schedule_timeout`,
with the rate limit healthy at 4911/5000. All four concurrency slots were holding claims whose
database connections had died with the restarting server, and nothing timed out.

**Cost.** Ingest stopped for roughly 19 hours. Days of history are only recoverable while GitHub
retains the logs.

**Mitigated 2026-09-02** by resetting the stranded claims and restarting; staleness returned to
seconds. **Not fixed** — the unit has no wait-for-Postgres, and the worker has no statement or
connect timeout that would turn a dead connection into an error instead of a permanent block.

**Closed by** both halves, in the same change as item 33: an `ExecStartPre` waiting on
`pg_isready` (bounded at 120s, so a genuinely dead database fails the unit rather than hanging
it), and `connect_timeout=10` on every connection so a server that is down or restarting raises
instead of blocking. The hang itself is covered by 33's watchdog, which bounds the job whatever
the cause.

### 33. ~~The queue has no claim lease~~ — **that was wrong** · RESOLVED 2026-09-03 · High

**Correction.** This entry claimed the queue had no claim lease. It does.
`queue.LEASE = timedelta(minutes=15)`, and `claim_next` already reclaims any `processing` row
whose `updated_at` is older than the lease. The four stranded rows *were* reclaimable from
12:00:43 onward. Nothing reclaimed them because **a lease only works if some worker is alive to
call `claim_next`, and the only worker was hung.**

The diagnosis was inverted: the lease is correct, and the hang is the entire bug.

**Fixed** by bounding every job. `worker.JOB_TIMEOUT = 10 minutes` wraps `_dispatch` in
`asyncio.wait_for`, so whatever blocks, the slot returns and the existing retry path runs.
`JOB_TIMEOUT < queue.LEASE` is a load-bearing ordering — a job outliving its lease could be
reclaimed and processed twice — and is asserted in `test_worker_watchdog.py`.

Also fixed alongside it, from the same incident:

- `db.connect()` now passes `connect_timeout=10`; psycopg's default is to wait indefinitely,
  which turns a database that is merely restarting into a stuck process.
- `deploy/cadence-worker.service` gained an `ExecStartPre` that waits on `pg_isready`, bounded
  at 120s. A *user* unit cannot reliably order itself `After=` a system service. This closes the
  boot race in item 32.
- `deploy/install.sh` was committed mode `100644`. `./deploy/install.sh` therefore failed for
  anyone who cloned the repo, including on the machine that wrote it. Now `100755`.

### 34. Four large-backfill jobs hang the worker deterministically · High

**What.** The 19-hour outage recurred on 2026-09-02, and the second occurrence identified it as
deterministic rather than random: **the same four job ids** — 459, 513, 519, 599 — were claimed
and hung again, with the same attempt counts. All four are `limit: 250` backfills of large
repositories (`denoland/deno`, `pytest-dev/pytest` ×2, `encode/django-rest-framework`), and all
four carry the same `last_error`:

> `API rate limit exceeded for user ID 98805408`

**Why it matters.** The upstream cause is item 27: the worker runs on a personal token and shares
one 5,000/hour budget with interactive `gh` use. A 250-run backfill of a large repo is thousands
of requests; three of them concurrently, against a budget someone else is also spending, exhausts
it. What is *not* explained is why exhaustion produces a silent hang rather than the `RateLimited`
exception the worker already handles — the journal holds no line between "Started" and the
restart, so none of the success or failure paths was reached.

**Mitigated** by item 33's watchdog: the job now times out at 10 minutes, releases its slot, and
retries with backoff until `MAX_ATTEMPTS` sends it to `failed`. The worker survives regardless.

**Not closed.** The mitigation stops one hung job taking the process down; it does not explain the
hang, and it does not stop these four jobs failing repeatedly. Closing it properly needs item 27
— a credential with its own rate limit — and then a look at what the provider does when a
rate-limit response arrives mid-pagination.

### 36. The two rules that could move Phase 1's criterion are unbuilt · Medium

**What.** Criterion 2 was re-measured 2026-09-03 at **median 2 findings** (target 3) and
**median 0.9% recoverable** (target 10%) over 49 repos — improved from median 1 / 0.0%, with
repos finding nothing down from 22 to 9. **One finding short on the median half.**

The movement came from ingest depth, not from new rules: runs per workflow stream went from a
median of 4 to 21 once the worker ran continuously. That source is now largely exhausted — 51%
of streams already clear `MIN_RUNS = 20`, so further depth yields less.

**Why the obvious candidate does not help.** `job_billing_rounding` was built expecting to move
this number. It does not, and correctly so: standard runners are free on public repos, the corpus
is entirely public, and the detector stays silent where nothing is billed. The criterion is
measured on public repos, so **only rules finding wall-clock waste can move it**, and that one
finds dollars.

**What is left, both specified and neither started:**

- **F8 · first-failing-step index** — for every failed job, the first step with a non-zero
  conclusion, aggregated. Deterministic; no ML, no gold labels, no log parsing. True regardless
  of who pays. Also the first stage of Phase 3, so it counts twice.
- **F6 · scheduled-workflow waste** — `schedule:` runs on days the default branch had no
  commits. Pure waste, invisible because it never fails.

Also unbuilt: `matrix_legs_never_independent`, measured but never implemented (item 29).

**Closes when.** One of F8 or F6 ships and the criterion is re-measured — not assumed to improve.

### 37. Suppression is designed, stored, and unreachable · High

**What.** `finding` has carried `status ('suppressed')`, `suppress_scope`, `suppressed_by` and
`suppressed_reason` since migration `001`. [`findings.py`](../src/cadence/findings.py) preserves
a suppression across re-audits and marks a returning finding `regressed`. The `dedupe_key`
design comment says waste findings key on `(rule, workflow_path, job_name)` *"so editing the
YAML does not orphan a suppression."*

**Nothing sets the column.** No ignore file, no inline comment, no CLI verb, no API. Every part
of the mechanism exists except the part a user touches. Found 2026-09-03 while comparing
against Infisical, which ships `.infisicalignore`, inline `infisical-scan:ignore`, and a
resolved/ignored/false-positive lifecycle.

**Why it matters.** It is a Phase 2 blocker, not a polish item.
[`PHASE_2_FIX_PRS.md`](phases/PHASE_2_FIX_PRS.md) anti-spam rule 3 — *"a closed PR permanently
suppresses that finding at `rule_repo` scope"* — cannot be implemented as written. Without it a
maintainer who declines a fix gets re-asked on every audit, which is the behaviour rule 4 of the
same section says is not recoverable from.

**What would close it.** `.cadenceignore` + inline `# cadence:ignore <rule_id> — <reason>` +
`cadence suppress/unsuppress`, with a mandatory reason and per-rule scope only. Design is F12 in
[`FEATURE_CANDIDATES.md`](FEATURE_CANDIDATES.md). Ship before the first fixer, and add the
Phase 2 ship criterion that tests it — a closed PR whose finding returns is the failure this is
meant to prevent.

### 38. Phase 6 overstated how novel verified liveness is · Low · CORRECTED 2026-09-03

**What.** [`PHASE_6_SECURITY.md`](phases/PHASE_6_SECURITY.md) §6A implied that no existing
scanner verifies whether a detected credential still works. GitGuardian ships validity
checking, and it is standard in the paid tier of that category. Infisical, checked the same
day, documents pattern matching, entropy and custom rules with no validation claim — so the
practice is common but not universal, and the original sentence was wrong either way.

**Why it matters.** Phase 6's precision argument leans on liveness, and a differentiation claim
that a reader can falsify in one search costs more credibility than the feature earns. The same
failure mode as items 31 and 33: a plausible diagnosis asserted before it was checked.

**Corrected to** the claim that survives: nobody verifies liveness **over CI log history**,
because nobody keeps CI logs. Make the claim about the corpus, not the technique.

## Environmental and tooling notes

### 20. Reddit is unreachable directly · Info

Both HTML and `.json` return a "Welcome to Reddit" interstitial from this environment.
**Workaround that works:** the [Arctic Shift](https://arctic-shift.photon-reddit.com)
public archive (`/api/posts/ids`, `/api/comments/search?link_id=t3_<id>`), with Reddit's
own `.rss` as a fallback. Redlib instances return 403. Recorded because this will be needed
again for field research.

### 21. `setup-uv` publishes no floating major tags past v7 · Info

`v8`, `v9`, `v10` exist only as exact releases, so `@v10` does not resolve and took all
jobs down once. Moot now that everything is SHA-pinned, but the same trap applies to any
action assumed to publish majors.

---

## Open questions that affect the plan

### 22. Phase 3's demand signal is weaker than the plan assumes · High

Flakiness is **15 of 1,546 HN comments and 6 of 96 r/devops comments**, against 288 for cost
and 229 for debuggability, plus an explicit in-thread scope rejection: *"A Dev team problem,
not CI/CD."* Three readings — sampling bias, flakiness being suffered privately, or Phase 3
genuinely being further from felt pain — and none is conclusive. **Not a reason to cut Phase
3.** It is a reason to read its kill criteria literally before committing seven weeks.

### 23. Stacked-PR detection needs two mandatory guards · Medium

Verified working (8 stacks in 100 open `vercel/next.js` PRs, including a 3-deep chain; 0 in
go/k8s/pytorch/polars). **Without both guards — same-repo parents only, and the default
branch never a parent — the detector labels 96–99 of 100 PRs as stacked**, because a fork PR
whose head branch is named `master` matches everything targeting the default branch. Needs
PR→run linkage that does not exist until Phase 5.

---

## Resolved

| Date | Item | Closed by |
|---|---|---|
| 2026-08-26 | Test suite was order-dependent; `TestReplayAtScale` asserted on whole-table counts and passed only by file ordering — while being the evidence for a Phase 0 ship criterion | `eb6fb5a` — fixture cleans on entry as well as teardown; verified across 5 seeds |
| 2026-08-26 | zizmor reported 20 findings / 9 high against our own workflows (`unpinned-uses`, `artipacked`) | `eb6fb5a` — SHA-pinned all 11 actions, `persist-credentials: false`, caching off on the release path |
| 2026-08-26 | CI could report green while silently skipping the 6 DB-backed test files | `eb6fb5a` — Postgres service, explicit reachability assert, and a skip guard |
| 2026-08-26 | Nothing verified migrations applied cleanly or idempotently | `eb6fb5a` — `migrations` job: fresh apply, no-op re-apply, every file recorded, core tables present |
| 2026-08-26 | Rate card understated self-hosted minutes; two cost paths disagreed for one runner | `b631bf6` — rate card 20260301 + reconciled fallback, 15 tests |
| 2026-08-26 | No security policy (Scorecard `SecurityPolicyID`) | `4278066` — [`SECURITY.md`](../SECURITY.md) |
| 2026-08-29 | Workflow config was fetched live and discarded, so no history existed and Phase 2's round-trip criterion was not reproducible | `94ede04` — migration 005 + `configstore.py`, content-addressed, verified end to end |
| 2026-08-29 | `history()`/`load_latest()` ordered on `now()`, which is *transaction* time in Postgres — rows written by one call share a timestamp and ordered non-deterministically | `94ede04` — all three queries tie-break on `id` |
| 2026-08-29 | `store_snapshot` read `row[0]` while `db.connect()` supplies dict rows, raising `KeyError: 0` on the production path while unit tests passed | `94ede04` — test fixture now mirrors `db.connect()`'s row factory; the divergence was why the bug was reachable |
| 2026-08-28 | Ingest stopped for ~4 days; no durable worker existed | `1066d66` — systemd user unit + install script; staleness 2d23h → 14s, zero failures |
| 2026-08-26 | Node 20 deprecation warnings on 3 actions | `eb6fb5a` — superseded by SHA pinning at current majors |
| 2026-08-26 | Adding a CI job silently weakened branch protection | `eb6fb5a` — protection requires only `ci-gate`, which aggregates every job |
