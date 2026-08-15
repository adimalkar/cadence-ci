# Phase 0 Audit — 12 findings, all fixed

Adversarial review of the Phase 0 ingest platform, run after it shipped. Every finding was
verified against real data or a failing test before being fixed; nothing here was accepted
on the strength of the review alone.

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
