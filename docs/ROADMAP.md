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
- [x] 50 repos ingesting continuously — demonstrated live, not just by structure: an
      unattended worker crossed a real 30-minute interval boundary, processed all 51 corpus
      repos in an 80-second burst with zero re-seeding, and rescheduled every one correctly
      (`poll_repo` done 211→262, +51 unattended; `AUDIT_PHASE_0.md` Phase 5)

**What's still not proven:** the mechanism firing correctly across one interval boundary is
not the same as a deployment running for weeks unattended. That still needs a long-lived
process (systemd unit, container, etc.) outside any single verification session's scope —
see the top pre-Phase-1 item below.

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
- [x] `no_dependency_cache` · `no_run_cancellation` · `false_needs_edge`
      (+ `cache_key_never_hits`, which fell out of the cache rule for free)
- [x] finding → evidence → savings, end to end, persisted and idempotent
- [ ] …→ **check run** — needs App write scope; CLI + report is the current surface
- [x] Versioned rate-card table (`rate_card_version` stamped on every finding)

**Week 6 — the simulator**
- [x] Job DAG build + levelling from `needs:` (`dag.py`, cycle-safe)
- [x] Critical path vs wall-clock vs theoretical floor
- [x] Queue time computed separately (queue-bound repos get the opposite advice)
- [x] Replay engine over stored step timings (`simulate.py`)
- [x] Replay/projection kept structurally apart — no code path sums them

**Week 7** — catalog classes A–C complete *(4 of ~14 rules done)*
**Week 8** — [x] cost model, two-currency reporting · [ ] start cold pitches
**Week 9** — classes D–E; [x] projection engine · [ ] corpus priors
**Week 10** — eval harness, calibration measurement

**Verified against real corpus data** (`cadence audit <repo> [--dry-run]`):
ruff, prettier, requests, flask, gin, django, numpy, vite, deno, cargo, terraform.
Findings are sparse (0–2/repo) with no misfires; the empty state reads
"No recoverable waste found. Pipeline is tight."

**Known limitation — reusable workflows.** `jobs.x.uses: ./.github/workflows/_build.yml`
renames runtime jobs to `x / <inner>`, matching nothing in the calling file. Config↔runtime
mapping coverage therefore ranges 18–100% across the corpus. Where coverage is <80% the
critical path is **withheld** rather than shown, because the wall-clock-vs-critical-path
gap would otherwise read as recoverable time when it is really unmeasured work.
Resolving this is the highest-value next task in Phase 1.

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

## Phase 5 — Merge readiness + grounded review · design in [`PHASE_5_REVIEW.md`](phases/PHASE_5_REVIEW.md)

**5A + 5B — weeks 14–16** (cheap, deterministic, no new infrastructure)
- [ ] Unresolved review threads via GraphQL; **live vs `isOutdated` bucketed separately**
- [ ] Check run `cadence/merge-readiness`, always `neutral` — never a gate
- [ ] Predicted merge conflicts from `/pulls/{n}/files` hunks — **no clone**; escalate to
      `git merge-tree` only if hunk overlap proves imprecise
- [ ] Lockfile semantic overlap (compare package entries, not text lines)
- [ ] Symmetric PR-pair dedupe key, or the same conflict reports twice

**5C — weeks 35–40** (BYO-key review; un-defers the KMS vault)
- [ ] Vault: envelope encryption, per-installation DEK under a KMS CMK, decrypt only in
      the worker, never returned over the API, logger redaction on the plaintext
- [ ] Rotation + revocation from day one
- [ ] **Silence rule enforced mechanically**: hunks with no Cadence evidence are omitted
      from the prompt entirely
- [ ] Prompt-injection defences: diff as delimited data, model output can trigger **no**
      action, structural validation before posting
- [ ] Hard cap 3 inline comments/PR; module defaults **off** when another review bot is
      detected; drop comments within ±5 lines of an existing bot comment
- [ ] Degrade to silence on provider failure — 5A/5B still ship

**Ship criteria**
- [ ] Lockfile conflict prediction ≥90% precision on 30 hand-labelled PR pairs
- [ ] **Zero** stored-key appearances in any log/API/error payload (test greps the plaintext)
- [ ] ≥20 hostile diffs produce no action and no reflected instruction text
- [ ] Every posted comment cites at least one piece of Cadence evidence
- [ ] With the provider hard-down, checks still post 5A/5B
- [ ] On a repo with CodeRabbit installed, the review module posts nothing

---

## Phase 6 — Security, AI-first · design in [`PHASE_6_SECURITY.md`](phases/PHASE_6_SECURITY.md)

**6A — weeks 17–19** (secrets; uses logs already stored)
- [ ] CI-log secret scanning — the surface no scanner covers
- [ ] `.ipynb` **output** scanning (secrets live in committed notebook JSON)
- [ ] Verified liveness against provider token-info endpoints (~60% → ~99% precision)
- [ ] `dedupe_key = hash(detector_id, sha256(value))`; plaintext never stored/logged/rendered
- [ ] Finding links the provider's revoke page — rotation is the action, not detection

**6B — weeks 31–34** (the AI rule pack)
- [ ] Model output → `eval`/`exec`/shell/SQL/path dataflow rules
- [ ] Unsanitised prompt interpolation · tool handler without authz · unpinned weights ·
      MCP exposure · RAG injection surface · unvalidated structured output
- [ ] **MITRE ATLAS** technique IDs on every rule
- [ ] Published as an open versioned ruleset (`cadence-ai-security`)

**6C — weeks 41–46** (general SCA + reachability; un-defers the sandbox)
- [ ] OSV.dev + GitHub Advisory DB; CycloneDX SBOM
- [ ] Reachability with **suppression as the headline**
- [ ] Confidence tiers: `unreachable (high confidence)` vs `(dynamic dispatch — verify)`
- [ ] Sandbox: no egress, read-only root, tmpfs, cpu/mem/pid caps, non-root, seccomp.
      **Threat model written before the runner.**

**Ship criteria**
- [ ] ≥95% precision on live-secret findings
- [ ] Zero plaintext secrets anywhere, verified by test
- [ ] Notebook detection catches a planted secret source-only scanners miss
- [ ] AI rule pack ≥85% precision on held-out real AI repos
- [ ] **No `unreachable (high confidence)` finding is ever wrong** on the eval set
- [ ] Sandbox escape-attempt test passes

---

## What Phases 5 and 6 cost — read before committing

They roughly **double the plan: 24 weeks → ~46**, or about 11 months at 15 hrs/week. Two
specific costs beyond calendar time:

1. **Both deferred hard items come back.** The KMS vault (5C) and the sandbox (6C) were
   pushed out of Phase 0 precisely because they were "the two hardest and slowest items."
2. **A new class of risk.** Losing run history is embarrassing; leaking a customer's model
   API key is an incident with someone else's bill on it.

**The sequencing above is deliberate**, not the order the features were requested in:

- **5A/5B (wk 14–16) and 6A (wk 17–19) come early** — 6 weeks total, no new
  infrastructure, and 6A runs on logs already in the database. Highest value per week in
  the whole plan.
- **5C and 6C are last** — they carry the vault and the sandbox. Pulling 5C forward is
  possible, but it front-loads the hardest security surface before the product has users
  to justify it.
- The **résumé line stays at week 13**. Nothing here moves it; these extend the product
  rather than completing it.

If the plan slips, cut in this order: 6C → 5C → 6B. The deterministic wedges (5A, 5B, 6A)
are the ones that earn their weeks.

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

**Promoted out of deferral on 2026-08-16** into Phases 5 and 6: secrets-in-CI-logs
(was [`EXPANSION.md`](EXPANSION.md) 1.1, now 6A), SCA/SBOM/reachability (was
[`PRODUCT_PLAN.md`](PRODUCT_PLAN.md) §8 and EXPANSION 2.5, now 6C), and static analysis
orchestration (now 6B/6C).

Still deferred, ranked in [`EXPANSION.md`](EXPANSION.md): preview-env cost attribution
(2wk), PR wait decomposition (2wk), test-impact advice (4wk).

Still explicitly rejected — see EXPANSION Tier 3 for the reasoning: merge queues, stacked
PRs, preview-env provisioning, runner hosting, build caching, and per-person velocity
metrics. Note that **workflow security scanning** stays rejected even though Phase 6 adds
security: zizmor, poutine, and StepSecurity's Harden-Runner already own *workflow*
hardening, and Harden-Runner has an agent inside the runner with strictly more data than
our read-only position can reach. Phase 6 scans your **source and dependencies**, not your
CI configuration.
