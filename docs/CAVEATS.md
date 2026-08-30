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
| 29 | `job_billing_rounding` and `matrix_legs_never_independent` are measured but unbuilt | Medium | Open |
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

**Closes when.** Both are implemented with tests, or explicitly dropped with a reason.

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
