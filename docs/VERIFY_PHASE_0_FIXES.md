# Plan: Verify the Phase 0 audit fixes against live data

**Status:** ready to execute · **Owner:** builder · **Est:** 2–3 hrs active, plus a 30-min
observation window in Phase 5

Context: [`AUDIT_PHASE_0.md`](AUDIT_PHASE_0.md) lists 12 bugs found after Phase 0 shipped,
all fixed in commit `8f00588`. Unit tests cover every fix. What is *not* yet established is
that they hold against live GitHub data and a real worker process — the drain that would
have shown this was interrupted when its background task was orphaned.

---

## The thing this plan exists to prevent

> **A clean corpus drain exercises only 4 of the 12 fixes.**

The other 8 live in failure paths — expired logs, permission denials, a crash mid-fetch,
partial webhook payloads. A happy-path poll never reaches them. "The drain was green" is
therefore *not* evidence those 8 work, and treating it as such would rebuild exactly the
false confidence that let 12 bugs ship under a green suite in the first place.

So the work is tiered by what each fix actually requires:

| Tier | Fixes | What it needs |
|---|---|---|
| **A** | F6, F7, F8, F12 | The natural corpus drain. Free. |
| **B** | F1, F5 | Targeted read-only probes against real GitHub. |
| **C** | F2, F3, F9 | Local webhook traffic. |
| **D** | F4, F10, F11 | Deliberate fault injection. |

Fix numbers below match `AUDIT_PHASE_0.md`.

---

## Preconditions

```bash
cd "/mnt/1TB_Drive/Data/MyFiles/Projects/Cadence System"
export CADENCE_GITHUB_TOKEN=$(gh auth token)
export CADENCE_TEST_DATABASE_URL=postgresql://localhost/cadence_test
```

- [ ] Postgres running (`pg_isready`)
- [ ] `git log -1` shows `8f00588` or a descendant — **all fixes must be present before
      any measurement**, or the numbers mean nothing
- [ ] `uv run pytest -q` → 82 passed
- [ ] `uv run ruff check src tests` → clean
- [ ] Migrations current: `uv run cadence db init` → "schema up to date"

**Stop the currently-running worker first.** As of writing, a `worker run --until-empty`
process is mid-drain. Two workers racing is *safe* (that is what `SKIP LOCKED` is for) but
it makes attribution impossible — you cannot tell which process handled which row.

```bash
pkill -f "cadence worker run"; sleep 2; pgrep -af "cadence worker" || echo "clear"
```

---

## Phase 0 — Snapshot the before-state

Every success criterion below is a *delta*. Capture the baseline first or the run is
unfalsifiable.

```bash
mkdir -p /tmp/cadence-verify
psql -d cadence -tAc "
SELECT 'reruns', count(*) FROM run WHERE run_attempt>1
UNION ALL SELECT 'runs_multi_attempt', count(DISTINCT run_id) FROM (
  SELECT run_id FROM job GROUP BY run_id HAVING count(DISTINCT attempt)>1) t
UNION ALL SELECT 'recovered_failures', count(*) FROM job j JOIN run r ON r.id=j.run_id
  WHERE r.run_attempt>1 AND j.attempt<r.run_attempt AND j.conclusion='failure'
UNION ALL SELECT 'name_differs', count(*) FROM job WHERE name<>name_base
UNION ALL SELECT 'jobs', count(*) FROM job
UNION ALL SELECT 'runs', count(*) FROM run
UNION ALL SELECT 'steps', count(*) FROM step
UNION ALL SELECT 'repos', count(*) FROM repo
UNION ALL SELECT 'job_null_started', count(*) FROM job WHERE started_at IS NULL
UNION ALL SELECT 'run_null_started', count(*) FROM run WHERE started_at IS NULL
" | tee /tmp/cadence-verify/before.txt
```

**Reference values at time of writing** (yours will differ if the interrupted drain
progressed further — that is fine, diff against your own baseline):

| metric | value |
|---|---|
| reruns | 23 |
| runs_multi_attempt | 5 |
| recovered_failures | 12 |
| name_differs | 446 |
| jobs | 27,005 |
| repos | 51 |

---

## Phase 1 — Tier A: the corpus drain

### 1.1 — Do NOT clear the stranded rows

There are ~6 rows sitting in `status='processing'`, left by the orphaned worker. **Leave
them.** They are a free, perfectly-formed natural experiment for F6 (lease reclaim): a real
worker died mid-job, and the fix claims a later worker reclaims the row once its 15-minute
lease expires. Deleting or resetting them by hand destroys the only live evidence for that
fix you are likely to get without staging a crash.

Confirm the leases are actually expired (they must be older than `queue.LEASE`, 15 min):

```bash
psql -d cadence -tAc "
SELECT count(*) AS stranded,
       count(*) FILTER (WHERE updated_at < now() - interval '15 minutes') AS reclaimable,
       min(updated_at)::timestamp(0) AS oldest
FROM ingest_job WHERE status='processing';"
```

`stranded` must equal `reclaimable`. If not, wait until it does.

### 1.2 — Force a full re-poll

ETags short-circuit unchanged repos with a 304, which would skip the very work being
verified.

```bash
psql -d cadence -c "UPDATE repo SET runs_etag=NULL;"
uv run cadence corpus seed --limit 40
uv run cadence queue status
```

### 1.3 — Run the drain so it cannot be orphaned again

The previous attempt was lost because it lived only as a child of an interactive session.
Detach it properly and leave a completion sentinel on disk:

```bash
rm -f /tmp/cadence-verify/drain.{log,done}
setsid nohup bash -c '
  cd "/mnt/1TB_Drive/Data/MyFiles/Projects/Cadence System"
  export CADENCE_GITHUB_TOKEN=$(gh auth token)
  uv run cadence worker run --until-empty --concurrency 5
  echo "exit=$?" > /tmp/cadence-verify/drain.done
' > /tmp/cadence-verify/drain.log 2>&1 < /dev/null &
```

Poll for completion rather than watching:

```bash
until [ -f /tmp/cadence-verify/drain.done ]; do sleep 10; done
cat /tmp/cadence-verify/drain.done   # must read exit=0
```

**Interruption is safe.** The queue is durable and every handler is idempotent
(`ON CONFLICT` throughout), so a killed drain can simply be re-run — that property is
itself worth confirming, and it is why no snapshot/restore step is needed here.

Expect roughly 2–4 minutes at concurrency 5 for 51 repos × 40 runs.

### 1.4 — Tier A success criteria

```bash
grep -ci "error\|traceback\|job_failed\|job_terminal" /tmp/cadence-verify/drain.log
psql -d cadence -tAc "
SELECT 'stuck_processing', count(*) FROM ingest_job WHERE status='processing'
UNION ALL SELECT 'failed', count(*) FROM ingest_job WHERE status='failed'
UNION ALL SELECT 'due_now', count(*) FROM ingest_job
  WHERE status='pending' AND run_at<=now()
UNION ALL SELECT 'scheduled_future', count(*) FROM ingest_job
  WHERE kind='poll_repo' AND status='pending' AND run_at>now();"
```

| # | Fix | Criterion |
|---|---|---|
| — | drain health | error count `0`; `drain.done` reads `exit=0` |
| **F6** | lease reclaim | `stuck_processing = 0`. The 6 stranded rows were reclaimed and completed by a live worker — **not** reset by hand |
| **F7** | recurring poll survives failure | `scheduled_future` = number of corpus repos (**51**), exactly one per repo. This is the fix that stops a repo silently going un-polled forever |
| — | drain terminates | `due_now = 0` |

Verify F7's *one per repo* precisely — a duplicate here means the fan-out guard in
`_schedule_next_poll` regressed:

```bash
psql -d cadence -tAc "
SELECT count(*) AS repos_with_duplicate_polls FROM (
  SELECT payload->>'owner' o, payload->>'name' n
  FROM ingest_job WHERE kind='poll_repo' AND status='pending'
  GROUP BY 1,2 HAVING count(*)>1) t;"   -- must be 0
```

Then F8 and F12, diffed against the baseline:

```bash
psql -d cadence -tAc "
SELECT 'runs_multi_attempt', count(DISTINCT run_id) FROM (
  SELECT run_id FROM job GROUP BY run_id HAVING count(DISTINCT attempt)>1) t
UNION ALL SELECT 'recovered_failures', count(*) FROM job j JOIN run r ON r.id=j.run_id
  WHERE r.run_attempt>1 AND j.attempt<r.run_attempt AND j.conclusion='failure'
UNION ALL SELECT 'name_differs', count(*) FROM job WHERE name<>name_base;"
```

| # | Fix | Criterion |
|---|---|---|
| **F8** | rerun attempts ingested | `runs_multi_attempt` ≥ baseline and **> 0**; ideally approaching the `reruns` count. Each is failing jobs that were previously invisible — Phase 3's gold labels |
| **F12** | job name preserved | `name_differs` ≥ baseline and **> 0** |

**F8 spot-check against the source of truth** — the single most valuable assertion in this
plan, because it compares our DB to GitHub rather than to itself:

```bash
R=31117272395   # encode/django-rest-framework, 3 attempts
psql -d cadence -tAc "SELECT attempt, count(*), count(*) FILTER (WHERE conclusion='failure')
  FROM job WHERE run_id=$R GROUP BY attempt ORDER BY attempt;"
gh api "/repos/encode/django-rest-framework/actions/runs/$R/attempts/1/jobs" \
  --jq '[.jobs[] | select(.conclusion=="failure")] | length'
```

Expected: 3 rows (attempts 1/2/3), 7 jobs each, with **4 failures on attempt 1** and 2 on
attempt 2 — matching the API exactly. If attempt 1 is absent, F8 has regressed.

> If this run has aged past GitHub's 90-day retention by the time you run this, pick
> another: `SELECT id FROM run WHERE run_attempt>2 LIMIT 5;` and adjust.

---

## Phase 2 — Tier B: targeted GitHub probes

Read-only, cheap, and they reach two failure paths the drain cannot.

### 2.1 — F1: expired logs return `None`, not an error

The corpus spans days; 410 needs a job older than 90 days. Find one deliberately:

```bash
OLD=$(gh api "/repos/prettier/prettier/actions/runs?per_page=1&created=<2026-01-01" \
      --jq '.workflow_runs[0].id')
JOB=$(gh api "/repos/prettier/prettier/actions/runs/$OLD/jobs" --jq '.jobs[0].id')
gh api -i "/repos/prettier/prettier/actions/jobs/$JOB/logs" 2>&1 | head -1
```

If that returns **410**, exercise our path against it:

```python
# uv run python - <<'EOF'
import asyncio, os
from cadence.providers import GitHubProvider
async def main():
    p = GitHubProvider(os.environ["CADENCE_GITHUB_TOKEN"])
    repo = await p.get_repo("prettier", "prettier")
    print("fetch_logs ->", await p.fetch_logs(repo, <JOB>))   # must print None
    await p.aclose()
asyncio.run(main())
# EOF
```

**Criterion:** prints `None`, raises nothing. Before the fix this hit `raise_for_status()`
and the job burned six retries before landing `failed`.

**Confidence: medium.** GitHub sometimes serves 404 rather than 410 for very old logs; a
404 also returns `None`, so the assertion holds either way, but note in the write-up which
status you actually observed.

### 2.2 — F5: a non-rate-limit 403 raises `AccessDenied`, not `RateLimited`

**Confidence: low that this reproduces on demand.** GitHub returns 404 (not 403) for
private repos the token cannot see, deliberately, to avoid leaking existence. Genuine 403s
come from SAML enforcement, org token policies, blocked repos, or disabled Actions — none
reliably available here.

Do not fake a pass. Either:

- **(a)** If a SAML-protected org repo is reachable, probe it and assert `AccessDenied`; or
- **(b)** Record F5 as **covered by unit test only**
  (`test_403_without_rate_limit_is_access_denied` and siblings in
  `tests/test_github_provider.py`), and say so plainly in the write-up.

(b) is an acceptable outcome. An honest "unit-tested, not reproduced live" beats a
manufactured probe.

---

## Phase 3 — Tier C: webhook path (F2, F3, F9)

**This is the highest-value tier after Tier A**, because F2/F3 are *latent* — polling only
ever sees completed jobs, so the corpus drain cannot touch them. They begin corrupting data
the day the first real App install lands.

### 3.1 — Start the receiver

```bash
export CADENCE_WEBHOOK_SECRET=dev-secret-for-local-verification
setsid nohup uv run cadence webhook serve --port 8787 \
  > /tmp/cadence-verify/webhook.log 2>&1 < /dev/null &
sleep 2 && curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8787/docs   # 200
```

### 3.2 — F2/F3: replay a job's real lifecycle

Send the same `workflow_job` id three times — `queued` → `in_progress` → `completed` —
mirroring what GitHub actually delivers. Only the last two carry `started_at`.

Build a script that, for a synthetic repo id (e.g. `909090`) and job id:

1. **queued** — `status: "queued"`, `started_at: null`, `completed_at: null`
2. **in_progress** — `status: "in_progress"`, `started_at` set, `completed_at: null`
3. **completed** — `status: "completed"`, `conclusion: "success"`, both timestamps set

Each signed with HMAC-SHA256 over the exact body, unique `X-GitHub-Delivery` per POST.
All three need `run_id`, `head_sha`, `workflow_name` (see `_job_payload` in
`tests/test_worker.py` for the exact shape). Drain between sends:

```bash
uv run cadence worker run --until-empty --concurrency 1
```

**Criteria:**

| # | Fix | Criterion |
|---|---|---|
| **F2** | job `started_at` fills in | After #1: `started_at IS NULL`. After #2: **non-NULL**. Before the fix it stayed NULL forever |
| **F2** | and never regresses | Re-send #1 last; `started_at` must **remain** set — the `coalesce` must not let a partial payload erase a known timestamp |
| **F3** | run `started_at` | Same shape via `workflow_run` events (`requested` → `completed`) |
| — | stub run | The `workflow_job` with no prior `workflow_run` creates a run row with `status IS NULL` — a job's outcome must never be recorded as the run's |

```sql
SELECT id, status, started_at, completed_at FROM job WHERE id=<JOB_ID>;
SELECT id, status, conclusion, started_at FROM run WHERE id=<RUN_ID>;
```

### 3.3 — F9: the receiver does not serialize on the DB

```bash
for i in $(seq 1 50); do <signed-post> & done; wait
```

**Criterion:** p95 response < 500 ms (the module's own non-negotiable rule — GitHub
disables slow endpoints). Before the fix, sync `psycopg` inside `async def` blocked the
event loop and serialized every delivery. Record the actual distribution, not just a
pass/fail.

### 3.4 — Clean up the synthetic data

```sql
DELETE FROM repo WHERE id=909090;   -- cascades to run/job/step
```

---

## Phase 4 — Tier D: fault injection (F4, F10, F11)

Cannot be triggered by real traffic. All local; none touch GitHub.

### 4.1 — F4: the ETag must not advance past unwritten data

The severest fix — a wrongly-advanced cursor loses step timings *permanently*, and the
symptom is a fast 304 that makes the pipeline look healthier the more it drops.

Already covered by `test_etag_is_not_stored_when_job_fetch_fails`. For live confirmation,
kill a worker mid-poll:

```bash
psql -d cadence -c "UPDATE repo SET runs_etag=NULL WHERE name='ruff';"
uv run cadence ingest astral-sh/ruff --limit 200 &   # then SIGKILL ~2s in
sleep 2 && kill -9 %1
psql -d cadence -tAc "SELECT runs_etag IS NULL FROM repo WHERE name='ruff';"
```

**Criterion:** `t` (still NULL). Then re-run the ingest to completion and confirm it
becomes non-NULL — the cursor advances only after a clean pass.

### 4.2 — F10: a mid-flight failure orphans no tasks

Point a provider fake at ~100 runs, raise on the 50th `fetch_jobs`, assert no
"Task exception was never retrieved" warnings and that all tasks are cancelled. Extend
`tests/test_ingest.py`; assert on captured warnings rather than eyeballing stderr.

### 4.3 — F11: concurrent identical log writes

```python
# N threads calling LocalLogStore.put(same_bytes) concurrently
```

**Criteria:** exactly one file on disk; it decompresses to the original bytes; no stray
`*.tmp` left behind; no `FileNotFoundError`. This is the retry case content-addressing
actively invites, so it is not hypothetical.

---

## Phase 5 — Confirm continuous ingest actually recurs

The one Phase 0 ship criterion still marked with a caveat in
[`ROADMAP.md`](ROADMAP.md): "50 repos ingesting *continuously*." The mechanism is
`_schedule_next_poll`, but it has never been observed firing unattended across an interval
boundary.

```bash
setsid nohup bash -c '
  cd "/mnt/1TB_Drive/Data/MyFiles/Projects/Cadence System"
  export CADENCE_GITHUB_TOKEN=$(gh auth token)
  uv run cadence worker run --concurrency 3
' > /tmp/cadence-verify/longrun.log 2>&1 < /dev/null &
```

Leave it up **35+ minutes** (`POLL_INTERVAL` is 30). Then:

```bash
psql -d cadence -tAc "
SELECT count(*) FILTER (WHERE status='done') AS completed,
       count(*) FILTER (WHERE status='pending' AND run_at>now()) AS rescheduled
FROM ingest_job WHERE kind='poll_repo';"
```

**Criterion:** `completed` grew without anyone re-seeding, and `rescheduled` still equals
the repo count. That is continuous ingest demonstrated rather than asserted — at which
point the ROADMAP caveat can be removed.

---

## Deliverable

Append a "Live verification" section to [`AUDIT_PHASE_0.md`](AUDIT_PHASE_0.md) with one row
per fix:

| Fix | Method | Evidence | Result |
|---|---|---|---|
| F8 | Tier A + API spot-check | attempt 1 shows 4/7 failures, matches API | ✅ |
| F5 | unit test only | not reproducible live — see 2.2 | ⚠️ |

Then commit with the before/after snapshots attached.

**Report honestly.** `⚠️ unit-tested only` is a fine outcome and belongs in the table. A row
marked ✅ on the strength of a probe that did not actually exercise the path is worse than
no row — that is precisely how 12 bugs cleared a green suite.

---

## Failure handling

| Symptom | Meaning | Action |
|---|---|---|
| `stuck_processing > 0` after drain | Lease reclaim (F6) regressed, or a worker died in the last 15 min | Confirm no worker is alive, wait out the lease, re-check before concluding |
| `scheduled_future < 51` | F7 regressed — repos will silently stop being polled | **Stop. Highest severity here** — it is invisible in normal operation |
| duplicate pending polls per repo | Fan-out guard regressed | Check `_schedule_next_poll`'s existence check |
| `runs_multi_attempt = 0` | F8 regressed | Confirm `run_attempt>1` runs exist at all before concluding |
| Drain exits non-zero | Read `drain.log` | Queue is durable — safe to re-run after diagnosis |
| Rate limited | Expected under repeated full re-polls | `gh api /rate_limit`; back off, resume — the queue survives |

---

## Explicitly out of scope

- Deploying a production long-lived worker (systemd unit / container). Phase 5 proves the
  *mechanism*; productionizing it is a separate task — and it is the **top pre-Phase-1
  item**, since every day without a running worker is a permanently lost day of history
  against GitHub's 90-day retention.
- Any Phase 1 detector work.
- Log-store backend migration to R2/B2.
