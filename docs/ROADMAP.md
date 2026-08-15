# Cadence — Execution Checklist

Design lives in [`PRODUCT.md`](PRODUCT.md) and the [`phases/`](phases/) docs. This file is
the checklist. 24 weeks, ~15 hrs/week. Tick items as they land.

**Slip rule:** any phase over 150% of budget gets cut to its deterministic core. Do not
extend.

---

## Phase 0 — Ingest platform · weeks 1–3

*No user-facing surface. Starts the history clock — the only irreversible deadline in the
plan (GitHub retains logs 90 days).*

- [x] `git init`, Apache-2.0 LICENSE, README with the §1 thesis
- [x] Project skeleton, `pyproject.toml`, dependency lock
- [x] Postgres schema: `repo`/`commit`/`run`/`job`/`step`/`log_chunk`
- [x] `finding` + `evidence` with the evidence `CHECK` and the insert trigger
- [x] `CIProvider` protocol; GitHub Actions the only implementation
- [x] Run + job + **step-timing** ingest with ETag conditional requests
- [x] Rate-limit handling: `Retry-After`, secondary limits, backoff
- [x] Log fetch + gzip to object store (content-addressed, fetched once ever)
- [x] Webhook receiver: HMAC-SHA256, `X-GitHub-Delivery` idempotency, 200 in <500ms
- [x] Job queue on Postgres (`FOR UPDATE SKIP LOCKED`)
- [x] 50-repo corpus ingesting on a 30-min delta poll (51 repos seeded and backfilled
      cleanly, 0 errors, ~2 min at concurrency 5; recurring re-enqueue proven — see
      note below)
- [x] `docs/HELDOUT.md` — 15 repos, never looked at again

**Ship criteria**
- [x] 1,000 webhook deliveries → zero drops, zero duplicates (replayed against the real
      ASGI app + Postgres, ~30% redeliveries interleaved — GitHub's own redelivery UI
      needs a deployed public endpoint this environment doesn't have; also verified once
      over live HTTP with real HMAC signatures)
- [x] Full backfill of a ≥500-run repo without tripping a rate limit
      (`react/react`, 500 runs / 7,524 jobs / 92,269 steps, 2:06)
- [x] Step timings reconstruct their span within 2% (0.03% mean on 1,401 ruff jobs, 0.09%
      on 266 prettier, 0.04% on 7,110 react jobs)
- [x] 50 repos ingesting — see note below for what "continuously" means here

**Note on "continuously":** each `poll_repo` job re-enqueues itself 30 minutes out
(`worker.py`), which is the actual mechanism of continuous ingest. Proving that mechanism
keeps running for weeks needs a long-lived deployment (systemd unit, container, etc.)
outside this session's scope — what's verified here is that the re-enqueue logic is correct
(tested) and that a full backfill pass across the corpus completes cleanly.

**Post-ship audit — 12 bugs found and fixed.** See [`AUDIT_PHASE_0.md`](AUDIT_PHASE_0.md).
Four were silent data-fidelity losses that shipped under a green 66-test suite, the worst
being that a re-run's earlier attempts were never ingested — dropping exactly the failing
jobs that are Phase 3's gold labels (verified: 6 recovered from a single DRF run). Suite is
now 82 tests. **Deploying a long-lived worker is now the top pre-Phase-1 task**, since
every day without one is a permanently lost day of history.

---

## Phase 1 — Waste audit · weeks 4–10

*The product. First user-facing surface.*

**Weeks 4–5 — prove the path on three rules before widening**
- [ ] `no_dependency_cache` · `no_run_cancellation` · `false_needs_edge`
- [ ] finding → evidence → savings → check run, end to end
- [ ] Versioned rate-card table (`rate_card_version` on every finding)

**Week 6 — the simulator**
- [ ] Job DAG build + levelling from `needs:`
- [ ] Critical path vs wall-clock vs theoretical minimum
- [ ] Queue time computed separately (queue-bound repos get the opposite advice)
- [ ] Replay engine over stored step timings

**Week 7** — catalog classes A–C complete
**Week 8** — cost model, two-currency reporting · **start cold pitches**
**Week 9** — classes D–E; projection engine with corpus priors
**Week 10** — eval harness, calibration measurement

**F0 + F1 — the audit report (weeks 8–10)** · design in [`FRONTEND.md`](FRONTEND.md)

The report is not a nice-to-have at the end of the phase — it **is** the cold-pitch
artifact from §10, so it has to exist the week pitching starts.

- [ ] F0: design tokens; the dimension-line waterfall component (shared by all 4 screens)
- [ ] F1: audit report — public, no auth, shareable URL
- [ ] Hero waterfall: actual vs floor, recoverable region hatched
- [ ] **Replay renders solid + point value; projection renders hatched + range.** The
      credibility rule from `PRODUCT.md` §6 made visible, not just documented.
- [ ] Every finding row: claim · evidence link · saving · basis · action
- [ ] Hover a job bar → queue time splits out as a leading segment
- [ ] Empty state as a real outcome: "No recoverable waste across 1,412 runs."
- [ ] Mobile — this link arrives in a GitHub issue, read on a phone

**Ship criteria**
- [ ] Audit runs across all 50 corpus repos unattended
- [ ] Median repo: ≥3 findings, ≥10% combined recoverable wall time
- [ ] Replay reconstructs known-good historical durations within 2%
- [ ] Zero findings without evidence (DB-enforced, test-verified)
- [ ] Report readable on a 375px viewport; every number reachable as text by screen reader
- [ ] **3 maintainers of repos we don't own confirm a finding surprised them**

---

## Phase 2 — Fix PRs · weeks 11–13

*Converts a report into a tool. Résumé line lands here.*

- [ ] Comment-preserving YAML round-trip editor
- [ ] **Round-trip test: 200 corpus workflows, byte-identical when no fix applied**
- [ ] Fixers: `cache.*`, `cache.key`, `cache.run_id_bug`, `concurrency.cancel`
- [ ] `preview()` returns `None` on unfamiliar shapes — declining is always correct
- [ ] Opt-in `pull_requests:write` / `contents:write`, separate from read scopes
- [ ] Anti-spam: 1 open PR max → 3 after first merge; report-first; closed = suppressed;
      **never an unsolicited PR on a read-only-ingested repo**
- [ ] Realized-savings writeback (30-day post-merge window)

**F2 — findings console (weeks 12–13)**
- [ ] Authenticated list: filter, sort, suppress with a reason
- [ ] "Open fix PR" from a finding
- [ ] **"This was wrong" button, prominent not buried** — it is the continuous eval stream
      that makes the calibration dashboard possible

**Ship criteria**
- [ ] Each fixer has a ≥20-workflow before/after corpus
- [ ] Round-trip test green
- [ ] **≥5 Cadence PRs merged in repos we don't own**
- [ ] Realized-vs-predicted recorded for every merged PR
- [ ] Zero uninvited PRs

---

## Phase 3 — Flaky build intelligence · weeks 14–20

*Four months of history already in the DB by the time this starts.*

- [ ] **Week 14 first task: query the corpus for gold-label count.** Expect ~30 reruns per
      1,000 builds, ~68% flaky. If under a few hundred, extend ingest before training.
- [ ] F1 build-level taxonomy: network · registry · rate-limit · OOM · concurrency ·
      external service (weeks 14–15)
- [ ] F2 log normalization + signature + clustering (16–17)
- [ ] **Golden corpus: 300 real failure logs → expected signature**
- [ ] F3 classifier: GBT, calibrated (isotonic/Platt) (18–19)
- [ ] Bootstrap from published datasets (`flaky-build.github.io`) before own gold set matures
- [ ] Week 18 decision: embeddings-as-features only if they beat tabular on held-out repos
- [ ] F4 blame candidates — ≤3 or nothing (20)

**Ship criteria**
- [ ] Golden corpus green
- [ ] F1 covers ≥80% of non-test flaky failures
- [ ] ≥85% flaky precision on ≥10 held-out repos
- [ ] Calibration within ±10% per decile
- [ ] Blame ≥70% precision, or emits nothing

---

## Phase 4 — Observability + trust · weeks 21–24

- [ ] Flaky-cost report — including infra flake (21)
- [ ] Feedback-loop decomposition: push → queue → execution → post (21–22)
- [ ] Changepoint regression detection, not fixed thresholds (22)
- [ ] DORA — one page, four queries (23)

**F3 — trends (21–22)** · **F4 — public calibration (24)**
- [ ] F3: flaky cost over time, feedback decomposition, regressions with introducing
      commit linked. One screen, not a dashboard suite.
- [ ] F4: **public calibration dashboard** — predicted vs realized per rule, replay and
      projection reported separately, published whether or not it flatters us

**Ship criteria**
- [ ] A maintainer says a number surprised them
- [ ] Calibration dashboard live, updating weekly, unattended
- [ ] Changepoint finds the introducing commit for ≥5 known regressions
- [ ] Feedback decomposition sums to wall-clock within 5%

---

## Continuous — from week 1

- [ ] Ingest running (every day of delay is a permanently lost day of history)
- [ ] Eval harness written in Phase 1, reused by every later module
- [ ] Weekly publish: calibration, precision, n, date, detector SHA
- [ ] **Week 8: re-verify Kleore's feature set.** If they ship config rules + fix PRs, only
      the simulator differentiates and this plan needs revisiting.

---

## Kill criteria

| Trigger | Action |
|---|---|
| <10% median recoverable wall time at wk 10 | Premise wrong. Stop before Phase 2. |
| Calibration <60% within ±25% at wk 13 | Drop dollar figures, ship raw diagnostics |
| <5 installs by wk 18 | Distribution problem. Stop building modules. |
| Flaky precision <75% at wk 20 | Deterministic core only, drop the classifier |
| Credential leak | All feature work stops until resolved and disclosed |

---

## Deferred (not cut)

Static analysis orchestration, SCA/SBOM, secrets-in-source, reachability — design intact in
[`PRODUCT_PLAN.md`](PRODUCT_PLAN.md) §8. Expansion candidates ranked in
[`EXPANSION.md`](EXPANSION.md); first four are secret-exposure-in-logs (2wk),
preview-env cost attribution (2wk), PR wait decomposition (2wk), test-impact advice (4wk).
