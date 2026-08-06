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
- [ ] Log fetch + gzip to object store (content-addressed, fetched once ever)
- [ ] Webhook receiver: HMAC-SHA256, `X-GitHub-Delivery` idempotency, 200 in <500ms
- [ ] Job queue on Postgres (`FOR UPDATE SKIP LOCKED`)
- [ ] 50-repo corpus ingesting on a 30-min delta poll
- [x] `docs/HELDOUT.md` — 15 repos, never looked at again

**Ship criteria**
- [ ] 1,000 webhook deliveries → zero drops, zero duplicates (verified by replay + diff)
- [ ] Full backfill of a ≥500-run repo without tripping a rate limit
- [x] Step timings reconstruct their span within 2% (0.03% mean on 1,401 ruff jobs, 0.09% on 266 prettier)
- [ ] 50 repos ingesting continuously

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
**Week 10** — eval harness, calibration measurement, report page

**Ship criteria**
- [ ] Audit runs across all 50 corpus repos unattended
- [ ] Median repo: ≥3 findings, ≥10% combined recoverable wall time
- [ ] Replay reconstructs known-good historical durations within 2%
- [ ] Zero findings without evidence (DB-enforced, test-verified)
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
- [ ] **Public calibration dashboard** (24) — replay vs projection reported separately

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
