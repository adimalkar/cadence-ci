# Phase 0 Audit — 12 findings, all fixed

Adversarial review of the Phase 0 ingest platform, run after it shipped. Every finding was
verified against real data or a failing test before being fixed; nothing here was accepted
on the strength of the review alone.

**Live verification against real GitHub data and a real worker process is below** — see
[Live verification](#live-verification). Executed per
[`VERIFY_PHASE_0_FIXES.md`](VERIFY_PHASE_0_FIXES.md): 11 of 12 fixes confirmed live, 1
confirmed as unit-tested-only exactly as that plan predicted.

**The pattern worth naming:** four of the top five are *silent* data-fidelity losses.
Nothing raised, nothing logged, and the pipeline kept reporting success while dropping data
GitHub deletes after 90 days. The test suite was green throughout. A pipeline that fails
loudly is a nuisance; one that goes quietly blind while reporting health is the actual
hazard, and Phase 0's job is to not do that.

---

## The four that mattered

### 1. A re-run's earlier attempts were never ingested — `ingest.py`

`fetch_jobs` was called without `attempt`, so GitHub's `filter=latest` returned only the
newest attempt. Every earlier attempt's jobs were dropped.

Those are precisely the flake gold labels: *"retry succeeded, no intervening commit"* is the
second-strongest signal in `PHASE_3_FLAKE.md`, and it lives entirely in the attempts that
were being discarded.

**Verified against live data** — `encode/django-rest-framework` run `31117272395`, 3
attempts:

| | jobs | failures |
|---|---|---|
| Before fix (stored) | 7 | **0** |
| After fix, attempt 1 | 7 | **4** |
| After fix, attempt 2 | 7 | **2** |
| After fix, attempt 3 | 7 | 0 |

Six failing jobs recovered from one run. Job IDs are distinct per attempt and `run_attempt`
is populated, so storing all attempts needed no schema change. Only reruns (~0.6% of runs)
pay the extra API calls.

### 2. The ETag cursor advanced before the data it covered was written — `ingest.py`

Page 1's ETag was committed alongside the run rows, *before* job fetching. A rate limit or
crash during job fetch rolled back the jobs but left the ETag durable — so the retry got a
304, returned early, and those runs' step timings were lost permanently.

Worst possible shape: silent, unrecoverable past 90 days, and it makes the pipeline look
*healthier* (a fast 304) the more data it loses. Fixed by storing the ETag only after jobs
and steps commit.

### 3 & 4. `ON CONFLICT` clauses dropped `started_at` — `ingest.py`

Neither `upsert_run` nor `upsert_job` updated `started_at`. Polling hid this because it
only ever sees completed jobs — but a webhook delivers a job three times
(`queued` → `in_progress` → `completed`) and only the later payloads carry `started_at`.
Every webhook-ingested job would have kept `started_at IS NULL` forever, silently voiding
`execution_time`, `queue_time`, and every billing figure downstream.

Latent rather than active — confirmed 0 affected rows in the current DB, because nothing
had been ingested by webhook yet. It would have started corrupting data the day the first
real App install landed.

Fixed with `coalesce(EXCLUDED.started_at, job.started_at)` — filling gaps without ever
letting a partial payload erase a timestamp already recorded.

---

## The rest

| # | Issue | Fix |
|---|---|---|
| 5 | 410 (log past retention) was unreachable — `_get` hit `raise_for_status()` first, so expired logs burned 6 retries then landed `failed` | `Expired` exception; terminal, returns `None` |
| 6 | Every 403 became `RateLimited`, but GitHub 403s for missing scopes/SAML/blocked repos too — and the worker's rate-limit path had no attempt ceiling, so those retried every 60s forever | `AccessDenied` split out on GitHub's own signals; `RateLimited` now capped at `MAX_ATTEMPTS` |
| 7 | No lease on claimed jobs — a killed worker stranded rows in `processing` permanently, and `--until-empty` hung forever counting them | 15-min lease, reclaimed by `claim_next`; expired leases excluded from the drain count |
| 8 | The recurring poll re-enqueued only on success, so ~6 min of GitHub trouble exhausted the retry budget and that repo **silently stopped being polled forever** | Re-enqueue moved to `finally`, with a duplicate guard against fan-out |
| 9 | Sync `psycopg.connect()` inside an `async def` endpoint blocked the event loop, serializing deliveries against the module's own sub-500ms rule | `asyncio.to_thread` |
| 10 | `asyncio.as_completed` orphaned ~95 in-flight tasks when one raised; they then errored against a closed client, unretrieved | Explicit cancel + gather on the failure path |
| 11 | Log temp path was digest-derived, so two workers storing byte-identical logs — the exact retry case content-addressing invites — collided and published a corrupt object | Temp name now unique per writer (pid + uuid) |
| 12 | `_parse_matrix` stripped parens from *any* job name, collapsing `deploy (staging)` and `deploy (production)` into one identity and merging unrelated timing distributions | Migration 003: keep both `name` (verbatim) and `name_base` (stripped) |

---

## One fixed in passing

`ingest_repo` accepts a caller's connection but assumed it was configured with
`dict_row` — it worked only because the CLI happened to pass one. Now uses an explicit row
factory, matching the discipline `queue.py` already had.

---

## What this says about the tests

All 12 shipped under a green suite of 66 tests. The gap is that those tests verified each
unit against the shape of data *the happy path produces* — completed jobs, successful
fetches, single attempts. Every one of these bugs lives in a second write, a partial
payload, or a failure midway.

Regression tests now cover each fix, and the ones that matter most (`test_ingest.py`)
drive the real `ingest_repo` against a provider fake that can fail mid-fetch and that
models `filter=latest` honestly, rather than asserting on hand-built rows.

Suite is 82 tests after the audit, up from 66.

---

## Live verification

Executed per [`VERIFY_PHASE_0_FIXES.md`](VERIFY_PHASE_0_FIXES.md). Unit tests establish a
fix is *correct in isolation*; this establishes it holds against real GitHub data, a real
worker process, and real concurrency — none of which a mocked test can fully stand in for.

**Result: 11 of 12 verified live. 1 (F5) confirmed as unit-tested-only** — predicted by the
plan itself, not a shortfall. One adjacent finding surfaced during F2 testing that is not
one of the original 12 (see below).

| Fix | Method | Evidence | Result |
|---|---|---|---|
| **F6** lease reclaim | Tier A, natural experiment | 6 rows genuinely stranded by a worker that died 5+ hrs earlier; a fresh worker reclaimed and completed all 6 (`stuck_processing: 6→0`), none reset by hand | ✅ verified live |
| **F7** recurring poll survives failure | Tier A drain | `scheduled_future = 51`, exactly one pending poll per corpus repo, `0` duplicates — even with old and freshly-seeded jobs colliding for the same repos | ✅ verified live |
| **F8** rerun attempts ingested | Tier A drain + direct API spot-check | `runs_multi_attempt` 5→7, `recovered_failures` 12→18. Spot-check against `encode/django-rest-framework` run `31117272395`: our DB shows attempt 1 = 7 jobs/4 failures; `gh api .../attempts/1/jobs` returns **4** failures independently — exact match | ✅ verified live |
| **F12** job name preserved | Tier A drain | `name_differs` (verbatim ≠ base) 446→5,838 across the re-polled corpus | ✅ verified live |
| **F1** expired logs return `None` | Tier B probe | Found a genuine 410 (not the 404 fallback) via `prettier/prettier`; `GitHubProvider.fetch_logs` returned `None`, raised nothing | ✅ verified live |
| **F5** `AccessDenied` vs `RateLimited` on 403 | Tier B probe attempted, fell back to (b) | No reachable 403 path: GitHub 404s repos a token can't see rather than 403ing, by design, and no SAML-enforced org was available. All 3 relevant unit tests pass (`test_403_without_rate_limit_signals_is_access_denied` and siblings) | ⚠️ unit-tested only |
| **F2** job `started_at` fills in, never regresses | Tier C, real webhook lifecycle | Sent `queued`→`in_progress`→`completed` as three separate signed deliveries: `started_at` was NULL after #1, non-NULL after #2. Re-sent the original NULL-`started_at` payload last (simulating late/out-of-order delivery): `started_at` **stayed set** | ✅ verified live |
| **F3** run `started_at` fills in | Tier C, real webhook lifecycle | Same shape via `workflow_run` (`requested`→`completed`): NULL after event 1, correctly `2026-08-15 22:45:05-04` after event 2 | ✅ verified live |
| — stub-run status | Tier C | A `workflow_job` webhook with no prior `workflow_run` created a stub run with `status`/`conclusion` both empty — never borrowed the job's outcome | ✅ verified live |
| **F9** receiver doesn't block on DB | Tier C, 50 concurrent deliveries | p50 108ms, **p95 120ms**, max 122ms, 50/50 under the 500ms budget. Spread this tight across 50 concurrent requests would not be possible if sync `psycopg` were still serializing the event loop | ✅ verified live |
| **F4** ETag cursor ordering | Tier D, real SIGKILL mid-poll | `SIGKILL`'d `cadence ingest astral-sh/ruff` ~1.5s in; `runs_etag` stayed `NULL` afterward. A subsequent clean pass set it correctly | ✅ verified live |
| **F10** mid-flight failure orphans no tasks | Tier D, injected `RateLimited` at run 50/100 | Only 61/100 `fetch_jobs` calls made (not 100); 20 in-flight tasks (matching `job_concurrency=20`) observed `CancelledError`; **0** unretrieved-exception warnings; **0** tasks alive after `ingest_repo` returned | ✅ verified live |
| **F11** concurrent identical log writes | Tier D, 20 threads, same bytes | Exactly 1 file on disk, correct `storage_key` agreement across all 20 writers, 0 leftover `.tmp` files, correct decompression, no `FileNotFoundError` | ✅ verified live |

### Before / after (Tier A drain)

| metric | before | after |
|---|---|---|
| `runs_multi_attempt` | 5 | 7 |
| `recovered_failures` | 12 | 18 |
| `name_differs` | 446 | 5,838 |
| `reruns` | 23 | 23 |
| jobs | 27,005 | 36,375 |
| runs | 3,043 | 3,877 |
| steps | 331,857 | 440,626 |
| repos | 51 | 51 |

### One adjacent finding — not one of the original 12

While verifying F2 (§3.2), the out-of-order re-send that confirmed `started_at` survives
also showed that `status` and `completed_at` **do** regress: the late `queued` payload (sent
after `completed`) reverted `status` from `completed` back to `queued` and blanked
`completed_at`. Only `started_at` is protected by `coalesce` — that was F2's specific scope,
and it holds — but `status`/`conclusion`/`completed_at` are unconditionally overwritten by
whatever event arrives most recently, with no ordering guarantee.

This is not a regression of any audited fix and GitHub does not appear to redeliver stale
states after a newer one in normal operation (only retries of the *same* failed delivery),
so it is low-severity today. Worth a fast-follow before webhook ingest is depended on for
anything status-sensitive: either drop truly-stale updates using `run_attempt` /
`workflow_job.id` sequencing, or accept the risk and document it. Not fixed here — this
verification pass scoped to the original 12.

### What live verification could not cover, and why

- **F7's "survives failure" property** was verified by structure (`finally` block,
  duplicate-guard test) and by the fact that the fan-out guard held under real concurrent
  seeding — not by directly killing GitHub's API mid-flight for 30+ minutes to watch a
  specific repo go quiet and recover. That would require a much longer, more disruptive
  session than this pass covered.
- **F5**, as predicted — see table above.
- **Continuous ingest across a real 30-minute interval boundary (Phase 5)** was started
  as a long-running background observation and is reported separately once the window
  closes, rather than delaying this write-up.
