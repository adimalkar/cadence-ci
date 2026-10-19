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
| 36 | Criterion 2's findings half **now passes** (median 3) once the audit reads the history we hold; recoverable still fails | **High** | Half open — re-measured 2026-09-06 |
| 37 | Finding suppression has four schema columns and no writer — Phase 2's anti-spam rule 3 is unimplementable | **High** | ✅ Resolved 2026-09-06 |
| 38 | Phase 6 overstated the novelty of verified liveness | Low | ✅ Corrected 2026-09-03 |
| 39 | Phase 4 doubled to 6 weeks when observability became a pillar — no kill criterion covers it | Medium | Open |
| 40 | F8's "infrastructure vs your tests" split was ~6x overstated — measured at 2.2%, not ~14% | Medium | ✅ Corrected 2026-09-06 |
| 41 | Every `savings=None` finding rendered as "config bug" in the replay swatch | Medium | ✅ Fixed 2026-09-06 |
| 42 | A stacked PR merged into an already-merged branch; its work never reached `main` | Medium | ✅ Recovered 2026-09-06 |
| 43 | F6 measured before building: fires on 4 of 51 repos, and its top hits are maintenance bots | Medium | ✅ Closed — not building |
| 44 | `evalsweep` measures the ship criterion with `irrelevant_path_trigger` structurally disabled | Medium | Open |
| 45 | Three of nine built rules fire on zero corpus repos | Medium | Open |
| 49 | The log archive holds 25 of 194,026 job logs — three docs claim we store every line | **High** | Open |
| 50 | Changed paths were fetched and discarded; `irrelevant_path_trigger` had no data | Medium | ✅ Fixed 2026-09-12 |
| 51 | `run.tree_sha` NULL on all 29,134 rows since migration 001, with an index built for it | Medium | ✅ Fixed 2026-09-12 |
| 52 | Directory-level fragility is noise — F13's headline measured at 1.33x and will not be built | Medium | ✅ Closed 2026-09-13 |
| 53 | Rate card billed self-hosted runners $0.002/min for a GitHub charge that never took effect | **High** | ✅ Fixed 2026-09-21 |
| 54 | Queue time was clamped silently on re-run jobs — up to 20.4% of a repo's observations | Medium | ✅ Fixed 2026-09-21 |
| 55 | `no_run_cancellation` titled 8 findings "no cancel-in-progress" when their file said otherwise | Medium | ✅ Fixed 2026-09-24 |
| 56 | Corpus measurements read a throwaway cache and printed empty tables as results when it vanished | Medium | Open |
| 57 | No-cancellation evidence points at line 1, even when a concurrency block sits at a known line | Low | Open |
| 46 | Criterion 2's recoverable half is unreachable on a public corpus — median headroom is 0.1% | **High** | Open — **kill criterion overridden 2026-09-07**, Phase 2 proceeding |
| 47 | Reusable-workflow mapping <80% on 16 of 50 repos, and it gates criterion 2 as well as the critical path | **High** | Open |
| 48 | Two Phase 1 items are not closable by engineering (App write scope, maintainer contact) | Medium | Carried to Phase 2 |
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

**Answered 2026-09-21, and the answer is that it was wrong — see item 53.** Filed as
"correct by construction and by unit test, not by observation." Observation was the thing
missing, and it would have caught it: the $0.002/min rate priced a GitHub charge that was
postponed indefinitely and never took effect. The unit tests all passed, because they
tested that the number resolved, not that the number was right.

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

### Update 2026-09-06 (second pass): the first measurement was under-powered

**The section below is kept because it was wrong in an instructive way.** It concluded that
F8 did not move the criterion. That was true of the *measurement*, not of the detector.

`build_context` defaulted to `limit_runs=200`. The corpus holds a **median of 545 runs per
repo** inside the same 90-day window, so the audit was reading about **37% of history
already in Postgres** — no API call saved, since the query is local.

Criterion 2 against how much history the audit is allowed to read:

| `limit_runs` | repos | median findings | ≥3 | zero | median recoverable | F8 fires |
|---:|---:|---:|---:|---:|---:|---:|
| 200 | 49 | 2.0 | 22 | 6 | 1.62% | 21/49 |
| **300** | 51 | **3.0** | 30 | 6 | 3.61% | 27/51 |
| 400 | 51 | 3.0 | 31 | 5 | 3.23% | 31/51 |
| 600 | 51 | 3.0 | 34 | 5 | 3.46% | 33/51 |
| 1000 | 52 | 3.0 | 35 | 4 | 3.38% | 39/52 |

**The findings half of criterion 2 passes at 300 and stays passing to 1000** — a plateau
across a 3.3x range, which is what distinguishes statistical power from a threshold picked
to make a number pass. F8 is what carries it: without F8 the median is 2.0 at every limit.

**Why 200 starved the detectors.** Their guards assume real history — `MIN_RUNS = 20` per
workflow stream, 150 for the matrix rule, `MIN_FAILURES = 20` for F8 — while 200 runs
*across all workflows* leaves most streams under threshold.
[`PHASE_1_WASTE_AUDIT.md`](phases/PHASE_1_WASTE_AUDIT.md) instructs detectors to "never
recommend removal below ~200 runs" **for one stream**; the context was handing them 200 for
the entire repo. 200 was an undocumented default in two call sites, never a decision.

**Changed** to `limit_runs=500` in `build_context`, `evalsweep.sweep` and the `audit` CLI —
enough to cover the median repo's full window without paying for the long tail.

**Still failing: recoverable.** 3.46% against a 10% target, and it plateaus too, so more
history will not fix it. That half needs rules that recover large wall-clock on the dominant
workflow, and no measured candidate does.

**The lesson worth keeping.** Three "this rule doesn't move the criterion" conclusions were
recorded before anyone asked whether the harness was feeding the detectors enough data to
fire. Check the measurement before concluding about the thing measured.

### Superseded 2026-09-06: F8 shipped, and the criterion did not move

Measured across 49 repos at the audit's own limits (90 days, 200 runs), before and after:

| | Before | After |
|---|---:|---:|
| **Median findings** | 2.0 | **2.0** |
| Repos with ≥3 findings | 17 | 22 |
| Repos finding nothing | 8 | 6 |
| **Median recoverable** | 1.62% | **1.62%** |

`first_failing_step` fires on **21 of 49** repos. It moves both tails — five more repos reach
three findings, two fewer find nothing — and **leaves the median exactly where it was**,
because the median repo is not among the 21. Recoverable is unchanged by construction: the
detector abstains from a savings figure.

**Why only 21.** Over all ingested history, 41 repos clear `MIN_FAILURES = 20`. Inside the
audit's 200-run window the failure counts are far smaller — the sorted distribution runs
`… 13, 14, 15, 15, 18, 23, 25 …` and the median repo has about 14. Only 23 repos clear the
floor at all; two more fail the concentration guard.

**The tempting fix is the wrong one.** Dropping `MIN_FAILURES` to 10 would make 33 repos
eligible and might well push the median to 3. It would also mean publishing *"40% of your
failures start here"* on the strength of four events. That is tuning a guard to pass a
criterion, which is the one thing the criterion exists to prevent. The floor stays at 20.

**What this means for the plan.** Three detectors in a row were expected to move criterion 2
and did not: `job_billing_rounding` (silent on a public corpus), `matrix_legs_never_independent`
(never built), and now `first_failing_step` (fires on 43% of repos, none of them median).
F6 is the last specified candidate, and it should be measured *before* it is built — the
question is not "is it a good rule" but "does it fire on the median repo".

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

**Resolved 2026-09-06**, migration `006` plus [`suppress.py`](../src/cadence/suppress.py).
Three surfaces — `.cadenceignore`, inline `# cadence:ignore <rule> — <reason>`, and
`cadence suppress add/remove/list`. Two rules made structural rather than conventional:

- **A reason is mandatory, enforced by a database CHECK** — not by review, the same
  discipline as the evidence trigger. A blank reason is rejected too.
- **No global scope exists.** `*` is a rule name like any other and silences nothing;
  a blanket mute is indistinguishable from uninstalling.

Anti-spam rule 3 is now testable and tested: `TestPhase2AntiSpamRule3` suppresses a finding
with `source='closed_pr'`, re-runs `persist_findings` with the same `dedupe_key`, and asserts
it stays suppressed. A declined fix cannot be re-proposed.

Findings are **suppressed, never withheld** from the database, so "what is silenced here and
why" stays answerable and un-suppressing needs no detector re-run.

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

### 39. Observability became a pillar and the schedule absorbed the cost quietly · Medium

**What.** On 2026-09-05 observability was promoted from a four-week retention phase to a
product pillar, split into export (4a) and surface (4b). The plan grew from 24 weeks to 26.
`PRODUCT.md`, `ROADMAP.md` and the phase file now agree, but three things were not
re-derived and should not be assumed:

1. **The 26-week figure is the old estimate plus two weeks.** 4a is new work — an OTLP
   exporter, a metrics endpoint, an event emitter and their tests — and two weeks for it is
   an assertion, not an estimate. The slip rule (150% of budget → cut to the deterministic
   core) applies; for this phase the core is 4a.
2. **No kill criterion covers Phase 4.** Every other phase has one in `ROADMAP.md`. A phase
   that is now a pillar and has none is the one most likely to sprawl — precisely what its
   own risk section warns about.
3. **The résumé line is still week 13**, and Phase 4 sits after Phase 3, so this adds two
   weeks to the *far* end. Worth confirming that is intended rather than accepted by
   default: the export half is arguably more demoable than Phase 3, whose demand signal
   item 22 already questions.

**Why it matters.** Phase 1's criterion 2 is still failing at roughly week 10 of a plan that
now runs to 26. Growing the far end of a schedule whose near end is behind is how plans stop
being believed. This entry exists so the growth is on the record rather than absorbed.

**What would close it.** Either a kill criterion for Phase 4 in `ROADMAP.md` with a week
attached, or an explicit decision to move 4a earlier — before Phase 3 — on the grounds that
it is cheap, differentiated, and unblocks Phase 6's audit stream.

**Also unresolved, and smaller.** The CI/CD semantic conventions Cadence would emit are
**Release Candidate, not stable** (checked 2026-09-05). Attribute names can still move, and
a user's dashboards break when they do with no change on their side. The phase file says to
pin the version and treat a bump as a breaking change; nothing enforces that yet.

### 40. F8's infrastructure split did not survive measurement · Medium · CORRECTED 2026-09-06

**What.** `FEATURE_CANDIDATES.md` F8 pitched the first-failing-step index around a split
between "your tests" and "infrastructure, not you", illustrated with `npm ci` at 9% and
`actions/checkout` at 5% — about 14% infrastructure.

Measured while building it, over 6,085 failed jobs with an identifiable first failing step
across 51 repos: **recognisable infrastructure is 2.2% of first failures**, carrying 2.5 of
495 hours. A deliberately generous allowlist — checkout, runner setup, `setup-*`, cache,
eight dependency-install commands, docker, artifacts, post steps — classified 2.2%. The
rest are project commands (`Run all tests on GPU`, `make test`, `Test without coverage`)
that no allowlist can name.

**Why it matters.** An illustration in a planning document read as a measurement and was
wrong by roughly 6x. Shipping that framing would have made the detector's headline a split
the data does not support — the same failure mode as items 31, 33 and 38, and the fourth
time a plausible number was asserted before being checked.

**Corrected to** what the data supports: **concentration**. The median repo's top failing
step is 38% of its failures; `pallets/flask` is 76% on one tox step. The detector leads with
that and reports the infrastructure share only as a secondary number, always with its
classification coverage attached, so an unclassified majority is never read as "not
infrastructure".

**Closed** — the correction is in `FEATURE_CANDIDATES.md` F8, in the detector's module
docstring, and asserted by `test_coverage_is_published_even_when_it_is_low`.

### 41. Every abstaining finding was labelled a config bug · Medium · FIXED 2026-09-06

**What.** Both the CLI table and the HTML report rendered `savings=None` as **"config
bug"**, and the report did it with the **replay swatch** — the solid marker `PRODUCT.md` §6
reserves for measured savings.

Three detectors abstain deliberately and none is a config bug: `long_tail_step` (the fix is
unspecified, so any number would be a guess), `job_billing_rounding` (the waste is billed
minutes, not wall clock) and now `first_failing_step` (the minutes were really spent). The
label asserted a claim no detector made, in the visual language of a measurement.

**Why it matters.** §6 exists so a reader can tell a measured saving from an estimate
without reading the words. An abstention wearing the replay swatch defeats that directly,
and it had been shipping since `long_tail_step` landed.

**Fixed** to "no time claimed" / "measured · no saving" with the projection swatch, in
`cli.py` and `report.py`, asserted by
`test_abstaining_finding_is_not_labelled_a_config_bug`. Found only because a third
abstaining detector made it obvious — an argument for adding rules that stress existing
presentation, not only new computation.

### 42. A stacked PR merged into an already-merged branch · Medium · RECOVERED 2026-09-06

**What.** PR #14 (the Phase 4 observability rewrite) was opened with `--base
docs/infisical-derived-features` while that PR was still open. When #12 merged, GitHub did
**not** retarget #14, because the repo has `delete_branch_on_merge: false` and the base
branch still existed. #14 then merged into that stale branch, and `main` never received the
commit. Caught a day later only because a `CAVEATS` summary row was missing.

**Why it matters.** Nothing failed. The PR reported merged, CI was green, and the work was
silently absent from `main` — the same class of failure as a wrong trace that renders fine.
Two PRs in a row hit it; #12 was retargeted by hand after noticing, #14 was not.

**Recovered** by cherry-picking `7ae0b1f` onto `main` in PR #15.

**Standing rule, so this does not recur.** Do not stack unless the parent is about to merge.
When stacking, retarget the base **by hand** the moment the parent merges, and verify with
`git merge-base --is-ancestor <merge-sha> origin/main` before believing a PR landed. The
claim "GitHub retargets automatically" is false for this repo's settings and should not be
repeated.

### 43. F6 measured before building, and should not be built · Medium · CLOSED 2026-09-06

**What.** `CAVEATS` 36 said F6 should be measured before it is built, and that the question
was not whether the rule is sound but whether it fires on the *median* repo. Measured
2026-09-06.

The signal is real. Consecutive scheduled runs of the same workflow with an identical
`head_sha` tested the same code twice — no commit API needed, the evidence is self-contained.
**74% of all scheduled runs (1,882 of 2,544) are redundant by that test**, and only **13
(0.7%)** produced a different outcome from the previous run. 99.3% reproduce a known result
on unchanged code.

**It still should not be built.**

| | |
|---|---|
| Repos with any redundant scheduled run | 30/51 |
| **Median waste per repo** | **0.13 h** — eight minutes over 90 days |
| Streams surviving a 5-minute median-duration floor | **6, across 4 repos** |
| Share of surviving hours in one repo's `daily.yml` | 165.9 of 257 h |

The top hits by volume are `close-stale.yml`, `keepalive.yml`, `lock.yml`,
`dependabot-triage`, `triage-scheduled-tasks` — **maintenance bots whose entire purpose is to
run on a clock regardless of commits.** Flagging them is a false positive, not a finding. A
5-minute floor separates them cleanly (the dropped set runs at 4s, 5s, 18s, 22s per run), and
leaves six streams across four repos.

**Closed as "will not build".** A rule reaching 8% of repos with a median of eight minutes
cannot justify its own maintenance, and it would ship a false-positive class we would then
have to suppress.

**Worth keeping from it:** the same-`head_sha` comparison is a good primitive, and the
5-minute floor is a clean, reasoned separator between test workflows and housekeeping bots.
If a scheduled-waste rule is ever revisited, start there.

### 44. The criterion harness runs with a rule switched off · Medium

**What.** `irrelevant_path_trigger` reads `ctx.changed_paths`, populated only by
`enrich_changed_paths`, which costs one API request per distinct commit and is therefore
opt-in. The `audit` CLI calls it. **`evalsweep.py` never does** — so every ship-criterion
measurement has been taken with one of nine rules structurally unable to fire.

**Why it matters.** The criterion is reported as the audit's yield. Reporting it with a rule
silently disabled by omission overstates nothing, but it means the number does not describe
the product being shipped, and nobody would notice from the output.

**Partially exonerating.** Probed on the six largest corpus repos with enrichment forced on
(60 runs each): **it still did not fire once.** So the omission is not why the rule is
silent — the rule is simply very narrow, or `MIN_RUNS = 30` per stream is unreachable at that
enrichment depth. Both are worth knowing and neither was known before.

**What would close it.** Either call `enrich_changed_paths` in the sweep and report its
coverage alongside the criterion, or delete the rule. A rule that fires on nothing is not
free: it is maintained, tested, and counted in "nine rules built".

### 45. Three of nine built rules fire on zero corpus repos · Medium

**What.** Per-rule reach across 49 audited repos at `limit_runs=200`:

| Rule | Fires on |
|---|---:|
| `no_run_cancellation` | 69.4% |
| `first_failing_step` | 42.9% |
| `long_tail_step` | 28.6% |
| `no_dependency_cache` | 18.4% |
| `cache_key_never_hits` | 8.2% |
| `false_needs_edge` | 6.1% |
| `non_discriminating_matrix_leg` | **0%** |
| `irrelevant_path_trigger` | **0%** |
| `job_billing_rounding` | **0%** |

**Diagnosed, and each is different.** `job_billing_rounding` is correct — the corpus is
public and standard runners are free, so it stays silent by design (item 24).
`irrelevant_path_trigger` is item 44. `non_discriminating_matrix_leg` requires `MIN_RUNS =
150` for one workflow stream while the audit read 200 runs across *all* streams; 21 repos
have a qualifying stream in the database and almost none did inside the context. Raising the
default to 500 (item 36) should help it, and that was not re-measured per-rule.

**Why it matters.** "Nine rules built" is the headline in `PROGRESS.md`, and six of them
produce every finding the corpus sees. That is worth knowing before adding a tenth.

**Re-measured at `limit_runs=500`, 51 repos, 2026-09-06:**

| Rule | at 200 | at 500 |
|---|---:|---:|
| `no_run_cancellation` | 69.4% | **76.5%** |
| `first_failing_step` | 42.9% | **66.7%** |
| `long_tail_step` | 28.6% | 27.5% |
| `no_dependency_cache` | 18.4% | 15.7% |
| `cache_key_never_hits` | 8.2% | 7.8% |
| `false_needs_edge` | 6.1% | 7.8% |
| `non_discriminating_matrix_leg` | **0%** | **3.9%** |
| `irrelevant_path_trigger` | 0% | **0%** |
| `job_billing_rounding` | 0% | 0% — correct, public corpus |

**Two of the three zero-firing rules are explained.** The matrix rule came alive at 2 repos
once it could see 150 runs on a stream, confirming the diagnosis. `job_billing_rounding`
stays silent by design (item 24).

**`irrelevant_path_trigger` is the one genuinely unexplained rule.** Zero at both limits,
and zero even with enrichment forced on the six largest repos (item 44). It is maintained,
tested, and counted in "nine rules built" while producing nothing.

**Still open.** Decide `irrelevant_path_trigger`'s fate — instrument it to report why it
withholds, or delete it. A rule that has never fired on 51 repos is not evidence of a clean
corpus; it is an untested code path.

Also worth noting: the reach numbers shifted slightly *down* for three rules at the higher
limit (`long_tail_step`, `no_dependency_cache`). More history means more runs failing a
consistency test, which is the guards working, not regressing.

### 46. Criterion 2's recoverable half cannot be reached on this corpus · High

**What.** Phase 1 ship criterion 2 wants ≥10% of median wall clock recovered. It sits at
**3.5%**, and every previous entry here assumed the gap was missing detectors. Measured
2026-09-07 across 50 repos, it is not.

Headroom — how far median wall clock sits above the theoretical floor, the slowest single
job — is the entire budget any parallelism rule can ever recover:

| | |
|---|---:|
| **Median headroom above floor** | **0.1%** |
| Repos already at their floor (<2%) | **29/50** |
| Repos with ≥10% headroom | 18/50 |
| …with ≥80% mapping coverage too | **3/50** |

**The median repo's entire run takes as long as its slowest job.** No rule can recover 10%
of wall clock from a pipeline already at its floor. This is unreachable by construction.

**Why it matters.** Three sessions were spent adding and measuring rules against this
number — `job_billing_rounding`, F6, `first_failing_step` — on the assumption that the right
rule would move it. The constraint was never the rules.

**What would close it.** Either fix reusable-workflow mapping (item 47), which is the only
path that keeps the criterion as written, or re-specify it: a corpus of mature public OSS
repos is already parallelised, and the waste Cadence finds there is billing and diagnostics
rather than wall clock. **That is a product decision and is deliberately not being taken
unilaterally here.**

**Note on the arithmetic.** 13 repos report ≥10% recovered against <10% headroom. Not
over-claiming — `no_run_cancellation` recovers whole superseded runs, which no single run's
floor bounds. Headroom is the wrong denominator for cancellation savings.

**Decision 2026-09-07: the kill criterion was overridden, not met.** Phase 2 proceeds with
this criterion failing, on the maintainer's explicit call, because Phase 2's own gate (≥5
merged Cadence PRs in repos we do not own) tests the same question against real people
rather than a corpus proxy.

**The obligation that comes with the override:** this criterion is **bypassed, not passed**.
No public claim about recoverable wall clock may cite it, and it must be re-measured against
a **private or billed** corpus — where the money is real and pipelines are less likely to be
already at their floor — before it is treated as answered either way.

### 47. Reusable-workflow mapping gates more than the critical path · High

**What.** 16 of 50 corpus repos map under 80% of their jobs to config nodes, so the critical
path and both waterfalls are withheld for them — correctly. What was not known until
2026-09-07 is that **the same gap drives criterion 2**: of the 18 repos that appear to have
≥10% recoverable headroom, 15 are below the threshold, with floors computed from the handful
of jobs we could place. `moby/moby` maps 2%, `rollup/rollup` 8%, `jestjs/jest` 12%.

**Why it matters.** It was filed as a presentation limitation. It is a measurement
limitation, and it is the binding constraint on Phase 1's remaining criterion.

**Cause, already known.** Reusable workflows (`jobs.x.uses: ./.github/workflows/_build.yml`)
rename their jobs to `x / <inner>`, matching nothing in the calling file.

**What would close it.** Resolve `uses:` references, parse the called workflow, and map the
`caller / inner` names back. It needs the called file, which `configstore` can already store.

### 48. Two Phase 1 items are not closable by engineering · Medium

**What.** Phase 1 closes with two items open that no amount of code will finish:

- **Check-run output** needs GitHub App write scope, which requires registering an App and
  a user installing it.
- **3 maintainers confirming a finding surprised them** needs contacting humans.

**Why it matters.** Both are real gates on whether the product works, not paperwork. The
second is the only criterion that tests whether Cadence told someone something true they did
not already know.

**Carried to Phase 2**, whose ship criterion 3 — ≥5 merged Cadence PRs in repos we do not own
— requires the same outreach and subsumes the maintainer confirmation. Recorded so Phase 1 is
not remembered as fully passed.

### 49. The log archive is empty, and three documents say otherwise · High

**What.** `EXPANSION.md:38`, `PHASE_6_SECURITY.md:50` and `FEATURE_CANDIDATES.md:37` all
state that Cadence "already stores every log line from every run."

Measured 2026-09-08: **25 job logs stored against 194,026 jobs. Zero of them from failed
jobs.**

The plumbing works — `cadence logs --failures-only` exists, the worker writes, the store is
content-addressed by sha. It has simply never been run at scale. Separately,
`LocalLogStore.get` has **no production callers at all**: logs are write-only, and
`evidence.log_chunk_id` / `byte_start` / `byte_end` exist for a `log_span` evidence kind no
detector produces.

**Why it matters.** Phase 6A's entire justification is *"it runs on logs Cadence already
ingests and stores. Nobody else has that corpus."* That is currently false. F11
(`expired_credential_failure`) is likewise marked "logs already stored". Both would fail the
moment someone tried to build them.

It also blocks failure→file attribution: 95% of first-failing steps are unclassifiable
project commands, so mapping a failure to a source file means reading stack traces out of
log text, and there is no log text to read.

**What would close it.** Run the backfill at corpus scale and measure what it costs against
the rate limit (item 27 is the standing constraint), then correct the three claims to say
what is actually stored. Until then, no plan should assume the archive exists.

### 50. Changed paths were fetched and thrown away · Medium · FIXED 2026-09-12

**What.** `enrich_changed_paths` fetched each commit's file list, handed it to the detectors
in memory and discarded it. Its dedupe cache was function-local, so a second audit re-paid
the full cost, and it sat behind a `--paths` flag that `evalsweep` never passes. That is why
`irrelevant_path_trigger` fired on **0 of 51 repos** — not a bad rule, a rule with no data.

**Fixed** by migration `007` and [`commitstore.py`](../src/cadence/commitstore.py): a
`repo_commit` table keyed `(repo_id, sha)`, populated by `cadence commits backfill`, read by
`build_context` with no flag and no API call at audit time.

Verified on `pallets/flask`: **269 of 271 runs now carry changed paths**, commit coverage
93/93. Previously zero in every sweep.

**Two things the store refuses to hand on.** GitHub caps a commit's file list at 300
entries, so a truncated list is recorded and then withheld — a rule concluding "nothing
relevant changed" from a partial list is wrong in the one direction that ships bad advice.
Empty commits are withheld for the same reason. `coverage()` returns both halves of the
fraction so anything derived from paths can publish its denominator, as the critical path
already does below 80% mapping.

### 51. `run.tree_sha` was NULL on every row, with an index waiting for it · Medium · FIXED 2026-09-12

**What.** Migration `001` created `run.tree_sha` and a partial index
`run_tree_sha_idx ... WHERE tree_sha IS NOT NULL`, and the schema comment calls it "the
strongest flaky label: same tree, different outcome". `github.py` set it to `None` on every
run with a comment saying it "costs an extra commit lookup and is backfilled separately".

**It was never backfilled.** NULL on all 29,134 rows; the index had no rows in it.

**Fixed as a side effect of item 50** — the same `GET /commits/{sha}` response that carries
the changed file list also carries `commit.tree.sha`, so populating it costs nothing extra
once the path backfill is being paid for. `backfill_tree_shas` fills runs that lack one and
never overwrites a value already present.

Verified on `pallets/flask`: **287 tree shas filled** on the first pass.

### 52. Directory-level fragility does not exist in the data · Medium · CLOSED 2026-09-13

**What.** F13's headline was *"changes under `src/auth/` fail CI 3.2× the repo average"*.
Measured across 7 repos with full commit coverage — 770 commits, 4,826 classified runs — in
four framings: 1.66× inclusive, **1.26×** with confounds removed, **0.79×** for
CI-config-vs-code, **1.33×** with exclusive attribution. Only 2 of 7 repos have any
directory at ≥1.5× their own base rate.

**Two things worth keeping.** The first measurement attributed a run to *every* directory it
touched, so mixed commits inherited their failures into every bucket — that is what inflated
`.github` to 2.73× and made the signal look real. And the tautology everyone assumes —
"editing CI config breaks CI" — is **false here**: CI-only runs fail at 0.79× the rate of
code-only runs, with numpy at 100 CI-only runs and zero failures.

**Why it matters.** A 1.33× median on base rates of 4–46% is noise with a decimal point. It
would have shipped as the headline of a whole feature.

**Stopped at three framings on purpose.** Cutting the same data until something clears a
threshold is fishing. Recorded here so a fourth cut is recognised as such.

**What it does not kill.** The commit-path store built to enable it stands: it gave
`irrelevant_path_trigger` data for the first time (item 50) and populated `run.tree_sha`
(item 51). Two of three justifications delivered; the third is closed.

**What survives of "where does this repo fail":** `first_failing_step` at step granularity,
67% reach. File granularity needs stack traces from failure logs, and item 49 says the log
archive holds 25 of 194,026.

### 53. The rate card invented a GitHub charge · High · FIXED 2026-09-21

**What.** Migration `004` states as fact: *"On 2026-03-01 GitHub began applying a
$0.002/min 'Actions cloud platform charge' to all workflow executions, self-hosted runners
included."*

**That never happened.** GitHub announced the charge on 2025-12-16 for self-hosted runners
in private repositories, and **postponed it indefinitely within 48 hours** after community
backlash. Verified 2026-09-21 against GitHub's own pricing-changes page and two independent
write-ups. There is no self-hosted platform charge today and no announced timeline.

What *did* happen, which card 2026 already had right: hosted rates fell up to 39% on
2026-01-01 (Linux x86 $0.008 → $0.006).

**Why it matters.** `__self_hosted__` is the fallback for *any* unrecognised runner label.
At $0.002/min it billed users for compute GitHub does not charge them for. `free_on_public
= true` made the error invisible on the corpus — all 55 repos are public — while it landed
squarely on **private repos with self-hosted runners**, which item 24 calls "the commercial
case". We were overstating the paying audience's dollar figures, which is the one direction
this project treats as unacceptable.

**How it was found.** Not by a test. A competitor's blog post cited the $0.002 fee while
researching Depot; verifying that citation surfaced the retraction. Three of the project's
worst errors (items 31, 33, 38) share the shape: a plausible fact asserted without being
checked. This one shipped in a customer-facing dollar figure.

**Fixed** by migration `008` — card 20260901 prices the sentinel at $0.000. Cards are
superseded, never rewritten, so a figure published under 20260301 still reproduces —
wrongly, but auditably.

**And turned into the feature it was blocking.** GitHub bills $0 for self-hosted, so every
CI cost tool reports $0, while the EC2 instance is real money nobody attributes.
`CostContext.self_hosted_usd_per_minute` lets an operator supply their own rate. `None`
stays $0 rather than becoming a guess, and it never overrides a known hosted rate — GitHub's
bill is not ours to restate.

### 54. Queue time was clamped silently · Medium · FIXED 2026-09-21

**What.** `aggregate_spans` computed `max(0.0, first_start - min(created))`. The clamp is
correct — a negative queue is impossible — but it was silent.

The negatives are entirely a re-run artifact: **9,179 of 9,182 affected jobs are in re-run
runs, zero in first-attempt runs**, with starts up to 90 hours before creation. GitHub
carries the previous attempt's jobs forward with their original `started_at` while
`created_at` advances. Same root cause as item 31.

**Why it matters.** Eight corpus repos are affected — **moby/moby at 20.4% of its jobs**,
astral-sh/uv at 8.7% — so a fifth of moby's queue observations were discarded with nothing
said. `queue_bound` under-fires on exactly the repos where re-runs are common, and that
verdict is load-bearing: it is the only vendor-neutral answer to *"will faster runners help
me?"*

**Fixed.** `NodeTiming.queue_unknown` records a clamped observation, `summarize_pipeline`
publishes `queue_coverage`, and `queue_bound` withholds unless every node's queue was
observable — the same discipline the critical path applies below 80% mapping.

**Detected per leg, not per node.** Legs `{created 100, started 130}` and `{created 150,
started 100}` aggregate to a clean zero that hides one leg being impossible; a contradictory
leg means that leg's timestamps came from another attempt, so the node's `min()` mixes
attempts.

**Note on my own measurement.** The ad-hoc SQL that found this reported queue shares like
**−223%** for microsoft/TypeScript, and I nearly reported that the product emitted negative
queue figures. It does not — `aggregate_spans` has always clamped. The defect was silence,
not wrong arithmetic, and the raw-SQL number was my error, not the product's.

### 55. The cancellation detector says "no cancel-in-progress" when there is one · Medium

**What.** `Workflow.cancel_in_progress` returns True only for a **literal** `true` — the parser
deliberately refuses to evaluate expressions. So a workflow with

```yaml
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
```

reads as not cancelling, and `no_run_cancellation` titles the finding *"N superseded runs
finished anyway — no cancel-in-progress in ci.yml"*. A maintainer reading that title can see
the key in their own file.

**Measured, and smaller than first written.** An earlier draft of this entry said the
detector mis-titles *"25 corpus workflows."* 25 is how many workflows **have** an expression
here (most often this PR-only form, e.g. astral-sh/ruff) — but the detector only **fires** on
2 of them; the rest cancel successfully and produce no finding. The same misleading title also
landed on workflows with `cancel-in-progress: false` (3) and group-only blocks (3). **8
findings of 94**, which matches the fixer's 8 "already declares concurrency" declines exactly.
The error was conflating a population with the findings emitted on it.

Found 2026-09-24 while evaluating the first fixer, which declined these correctly ("already
declares concurrency") — so no bad fix PR results from it. The defect is the detector's
**wording**, not the fixer.

**Why it matters, and how much.** The savings figure is close to right, **measured**:
`find_superseded_runs` ignores conclusion, so runs the expression genuinely cancelled are
still counted — but only for the cancellation latency. On the corpus they are 1 of 46
superseded runs and **51 of 4,195 claimed seconds (1.2%)**. What remains is superseded pushes,
which the expression genuinely lets finish. The **claim**, though, is false, and a false claim
a reader can verify in ten seconds costs trust in every other number on the page.

**The tempting fix is the wrong one.** Evaluating the expression would make the parser guess
at GitHub's semantics, which the docstring rules out for good reason. And treating any
expression as "cancels" would silence real waste on the push side.

**What would close it.** Keep the refusal to evaluate, and make the finding say what is
actually known: when `cancel-in-progress` is an expression, retitle to *"cancel-in-progress is
conditional; N superseded runs still finished"*, and describe the saving as coming from the
runs the condition leaves running. Small, deterministic, needs its own tests.

**Fixed 2026-09-24**, detector version `no_run_cancellation@2`. `cancellation_state()`
classifies the config into five states and each gets its own title, suggested action and
evidence — without ever evaluating an expression:

| State | n | Title now says |
|---|---:|---|
| absent | 86 | *no cancel-in-progress* — unchanged, and true |
| explicit `false` | 3 | *cancel-in-progress is set to false* |
| group only | 3 | *concurrency groups runs but does not cancel them* |
| conditional | 2 | *cancel-in-progress is conditional* |
| unrecognised | 0 | *has a value we could not interpret* (e.g. a quoted `"true"`) |

Three details worth keeping. The **evidence payload** used to say `{"missing": ...}` even
when the key was present; it now states the actual value. The **suggested action** for
`false` and conditional offers **suppression with a reason** as a first-class answer, because
those settings are often deliberate — runs that must always finish. And the version bump is
not cosmetic: persisted findings carry the old wording and must stay attributable to it.

**Related trade-off, recorded so it is not rediscovered.** The same parser rule is why
`concurrency.add` inserts a literal `true` rather than the PR-only expression: the expression
would leave the detector unsatisfied and the fix would be re-proposed after merging. So the
fixer cancels superseded default-branch runs too — correct for CI, and the reason
deploy-shaped workflows are declined. A maintainer who hand-edits to the PR-only form will see
this detector fire again, and can suppress it with a reason (item 37).

### 56. Measurements read a cache that vanishes, and fail silently when it does · Medium

**What.** Every corpus measurement since Phase 1 — the recoverable-criterion curve, the F6
and fragility kills, the fixer evaluation — needs each repo's workflow YAML, and fetches it
into a throwaway directory under `/tmp`. That directory does not survive between sessions.

**The failure it caused.** On 2026-09-24 a measurement globbed a cache directory that had been
cleaned, found no files, and **printed an empty table** — which reads exactly like "the corpus
has no workflows in any of these states." It was caught only because an empty result for a
55-repo corpus looked implausible. A result that is merely *smaller* than expected would not
have been.

**Why it keeps happening.** Workflow YAML *has* a durable home — `workflow_snapshot` and
`workflow_blob` in Postgres (migration `005`) — but item 28 records that snapshots are captured
only by `cadence audit`, never by ingest. So for most of the corpus the database holds no
config, every measurement re-fetches roughly 150 API calls into a scratch directory, and each
one is exposed to the same silent-empty failure.

**Mitigated for now.** The fetch helper moved to `~/.cache/cadence/`, which survives `/tmp`
cleanup, and refuses to continue on an empty cache rather than reporting nothing as a result.

**What would close it.** Capture workflow snapshots during ingest (which also closes item 28),
read measurements from Postgres, and give every measurement script a non-empty assertion on
its input. The last part is cheap and should apply to any script that could silently produce
zero rows.

### 57. No-cancellation evidence points at line 1 · Low

**What.** `no_run_cancellation` emits a `code_range` evidence item with `line_start=1,
line_end=1` regardless of the file. When there is no concurrency block that is fair — there is
nothing to point at. But for the 8 findings where a block exists (item 55), it sits at a known
line, and the evidence should point there.

**Why it is not fixed yet.** `Workflow` does not record where `concurrency:` sits; the parser
keeps line numbers for jobs and steps only. Adding it is a small parser change with its own
tests, and it is a presentation improvement rather than a correctness one.

**What would close it.** Track the top-level `concurrency:` line in `parse_workflow`, and use
it for the evidence range when the block exists.

### 58. `cache_key_never_hits` was wrong on every corpus finding · High

**What.** The rule flagged any `actions/cache` step whose key interpolates `github.run_id`,
`run_number`, `run_attempt` or `sha`, at confidence 0.98, titled *"written every run, restored
never."* Measured on the 55-repo corpus (2026-09-24): **9 steps in 4 repos, and all 9 were
working caches.** Those 4 repos are the rule's entire 7.8% reach in item 45.

| Shape | Steps | Repos | Why it hits |
|---|---:|---|---|
| `restore-keys:` prefix | 7 | pytest, cpython, webpack | restores the newest entry by prefix and saves a fresh one; the documented pattern for incremental caches (jest, hypothesis, HTTP) |
| same-run handoff | 2 | moby | `build-dev` saves `dev-image-<arch>-<run_id>`, `validate` restores `dev-image-amd64-<run_id>` later in the same run |

Also found: cpython's key is `${{ github.head_ref || github.run_id }}`. On a pull request
that key is per-branch, so the regex matched a word the key mentions, not what the key is.

**Why it got through.** The rule relied on config alone, so nothing ever checked it against a
real cache. Its test fixture was the one shape (bare `run_id`, no `restore-keys`) that is
actually broken, and the Phase 2 plan filed the fix under *"Unambiguous bug; single-line fix."*
A fixer built on it would have rewritten 9 working caches in 4 repositories.

**Fixed, `dependency_cache@2`.**
- The rule now stays silent when the step has any `restore-keys`.
- It also stays silent when any `actions/cache` or `actions/cache/restore` step in any of the
  repo's workflows can read the key back, whether by exact key or by `restore-keys` prefix.
  Non-unique expressions are wildcards, so the matrix leg matches `amd64`.
- It counts only whole expressions: `a || github.run_id` is not per-run.
- The title now names the scope (run, attempt or commit). A `run_id` key *does* restore on a
  re-run, so "restored never" was untrue there too.
- Confidence drops from 0.98 to 0.9. 13 regression tests cover it, 6 of them the corpus
  shapes.

**What remains open.**
- **The rule now fires on 0 of 55 repos.** It joins the zero-reach rules in item 45. The
  `cache.run_id_bug` fixer has no target in the corpus and moves to the back of Phase 2.
- **Restorers we cannot see still silence nothing.** A composite action or a reusable
  workflow in another repository that restores the key is invisible, the same blind spot as
  item 47.
- **Wildcarding over-silences.** Two parallel jobs with copy-pasted per-run keys will match
  each other and suppress a real finding. The error is deliberate: this rule tells people a
  cache is dead, so silence is the safer mistake.
- **The broader lesson:** every config-only rule is still unchecked against the corpus. That
  is `no_run_cancellation` (its wording was already wrong once, item 55), `false_needs_edge`'s
  config verdict, and `irrelevant_path_trigger`. Each needs the same labelled sample of real
  hits before it gets a fixer.

### 59. `actions/setup-node` counts as a cache even without `cache:` · Medium

**What.** `_CACHE_EQUIVALENT` in `detectors/cache.py` lists `actions/setup-node`, so any job
that uses it is "cached" and `no_dependency_cache` never looks at it. On the corpus, **84 of
513 jobs with an install step (15 repos)** are silenced this way: setup-node with no `cache:`
input and no other caching. That is more than the rule's whole current reach.

**Why it is not fixed here.** From v5, setup-node can enable npm caching automatically,
depending on `package.json`, which the audit does not read. The corpus pins are v6, v7 and
SHAs. Deleting the entry could turn real caches into findings. The duration-flatness gate
would catch most of those, but that is a guess, not a measurement.

**What would close it.** Confirm the exact v5+ auto-cache condition from setup-node's source,
not a changelog summary. Then either read `package.json` (one more API call per repo) or treat
v5+ without `cache:` as unknown rather than cached. Finally, re-measure the finding delta
against real timing series before it ships.

**Fixed, `dependency_cache@3` (2026-09-24).** The condition, read from setup-node's
`src/main.ts`: without `cache:`, it caches only when it is v5.0.0 or later,
`package-manager-cache` isn't `false`, and the **workspace-root** `package.json` names npm in
`devEngines.packageManager` or `packageManager` (`/^(\^)?npm(@.*)?$/`). pnpm and yarn get no
auto-cache. Every corpus pin is v6 or v7 (the SHAs resolve to v7.0.0 and v6.3.0), so the
84 jobs came down to the file:

| Actual state | Jobs |
|---|---:|
| auto-cached, root `package.json` names npm (TypeScript only) | 19 |
| `package-manager-cache: false` set explicitly | 20 |
| `package.json` names pnpm or yarn, or has no `packageManager` | 37 |
| no root `package.json` | 8 |

**65 of 84 were counted as cached and aren't.** The root `package.json` is now fetched, one
request, and only when a setup-node step's caching depends on it. It is tri-state:
- *unfetched* keeps the old answer;
- *absent* means no auto-cache;
- a failed request stays *unfetched*, never *absent*, so a network blip cannot turn every
  setup-node job into a candidate.

A setup-node step that runs before any checkout reads nothing. A checkout into `path:` or of
another `repository:` counts as unknown.

**Measured effect**, whole corpus against stored timing: `no_dependency_cache` goes from
**45 to 50 findings, 7 to 11 of 53 repos**. The new findings are babel `test262` (19–22 s/run),
eslint `build` (31–37 s), vite (8–9 s) and ruff ×2 (5–6 s). Only 5 of the 65 newly examined
jobs fire: 26 have no timing series under their job key, 5 have fewer than 5 observations, and
1 is bimodal. The timing gate is doing its job, and the reach gain is small because the timing
data is thin, not because the config is right.

### 60. `actions/cache/save` is invisible to both cache rules · Low

**What.** The corpus has 199 `actions/cache` steps (25 repos), 126 `actions/cache/restore`
(10 repos) and 64 `actions/cache/save` (10 repos). Save steps count neither as "has caching"
in `_job_has_caching` nor as a candidate in `_never_hits`. Measured: 53 save steps use a
per-run or per-commit key, and **all 53 are read back** by a restore step, so extending the
never-hits rule to them would find nothing today.

**Related, unmeasured.** The `restore-keys` pattern that item 58 now treats as healthy still
saves a new entry every run. In a busy repository that fills the 10 GB cache and evicts the
entries other jobs need. That is the `cache_evicted_before_reuse` candidate in
`PHASE_2_FIX_PRS.md`, which needs the cache-usage API, not config.

**What would close it.** Add `actions/cache/save` to the caching-equivalent set (it needs a
restorer in the same job to be useful, so check for one), and add it to the never-hits
candidates, where the handoff check already covers it.

### 61. `no_dependency_cache` prices work that is not a dependency install · High

**What.** Found while measuring item 59. On the corpus, **about 18 of 50 findings are
wrong:**

| Cause | Findings | Example |
|---|---:|---|
| `apt-get install` counts as a dependency install | 16 | redis ×15, numpy ×1 |
| the install shares its step with a build or check | 2 | numpy `Meson Build` (`docker run …`), requests `Run pre-commit` |

The projection is taken from the **whole step's** duration. redis's `test` step runs
`sudo apt-get install tcl8.6 tclx` followed by `./runtest`, so the finding says caching would
save **594–705 s per run**. That is the test suite. Its suggested fix, "actions/cache keyed on
your lockfile", cannot apply to apt packages either.

The other 32 are single-purpose install steps (`pnpm install`, `pip install`, `npm ci`,
`uv sync`), where the step duration is the install duration. 3 numpy steps include a
`pip uninstall` or `python --version`; those are counted as clean.

**Why it matters now.** The `cache.*` fixer would write a lockfile-keyed cache into these
jobs, and redis's findings alone carry the largest projected savings on the corpus.

**What would close it.** Take `apt-get install` out of `_INSTALL_PATTERNS`: system packages
are a different fix (a pre-built image or `cache-apt-pkgs-action`) and a different finding,
if one at all. Then only project from a step whose `run:` is the install and nothing heavier.
When the install shares a step, either decline or say the number is an upper bound. Re-measure
the 50.

The same run also found that the dedupe key `no_dependency_cache:{path}:{job}` has no repo in
it. It is unique within a repo, which is where it is used, but any cross-repo aggregation keyed
on it silently merges findings (it did in the measurement script, and was caught only by a
repo count that did not add up).

**Fixed, `dependency_cache@4` (2026-09-25).** Three causes, not the two first diagnosed:

1. **`apt-get install` removed** from the install patterns.
2. **Only install-only steps are priced.** A step qualifies when every command in it is a
   dependency install or bookkeeping: `cd`, `echo`, version probes, `pip uninstall`, and the
   package manager updating itself. Anything else, a build, a test or apt, and the step is
   skipped in favour of a later pure one, or the job declines.
3. **The timing lookup was wrong in two ways.**
   - Series were keyed by the job's *display* name, and the detector looked them up by
     *config key*, so any job with `name:` never matched.
   - On a miss it fell back to **the job's longest-running step**. Series were also merged
     across every workflow with a same-named job.

   Timings are now resolved to config with `job_for_runtime_name` (the mapping the run DAG
   already uses), inside the run's own workflow. Unnamed steps are matched by GitHub's
   default `Run <first line>`. The fallback is gone: a series from another step is never
   evidence about this one.

Lookup coverage across 488 install-step jobs:

| Before | After |
|---|---|
| 137 matched exactly | **273** matched their own step |
| 40 priced from the longest step in the job | 0 |
| 311 not found | 215 not found; they decline |

**Measured effect** against #29's code, both with the real `package.json`: the count stays at
50 but the composition turned over, and repos with a finding went from 11 to 9 of 53.

| Change | Findings | What they were |
|---|---:|---|
| removed | 21 | **all wrong:** redis ×15 (apt, `make`, the test suite at 594–931 s), numpy `Meson Build` and its apt+pip benchmark step, requests `Run pre-commit`, django priced from `flake8`, fastapi priced from `Upload coverage to Smokeshow`, and ruff `publish-playground.yml`, priced from a *different* workflow with the same job and step names |
| added | 21 | genuine install steps the old lookup could not reach: eslint ×5, remix ×10, django-rest-framework ×2, vscode, numpy ×3 |
| repriced | 10 | angular ~1 s each, once workflows stopped being merged |

**Still open.** Item 62 (the long-tail rule reads the same merged series) and item 63 (half
the findings sit at the savings floor).

### 62. `long_tail_step` ranks series merged across workflows · Medium

**What.** `ctx.step_series` is still keyed by (job display name, step name) and merged across
every workflow with a job of that name. Item 61 moved the cache rule to the resolved index
and deliberately left this one alone, because re-keying changes the long-tail rule's titles and
dedupe keys. But the same merge that priced ruff's `publish-playground.yml` from another
workflow's runs also feeds the long tail. A `build` job in `ci.yml` and a `build` job in
`release.yml` rank as one step. **Not measured.**

**What would close it.** Move `long_tail_step` to `step_series_resolved`, with the dedupe key
`long_tail_step:{workflow}:{job}:{step}`, and measure the change in its 27.5% reach.

### 63. Half the cache findings sit at the 5-second floor · Low

**What.** After item 61, 50 findings remain, and remix alone has 10 at a 5–6 s projected
saving each. The floor is `savings.high >= 5.0` s per run, never argued with evidence. It
decides whether a finding appears at all, and at 5 s a fixer PR would be noise to a
maintainer. The fixer's minimum is a separate question from the detector's, and neither is
set.

**What would close it.** Set the detector floor from where maintainers act. When the `cache.*`
fixer is built, give it its own minimum, higher than the detector's, so small findings stay
visible in the report without generating PRs.

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
