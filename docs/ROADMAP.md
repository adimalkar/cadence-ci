# Cadence — Roadmap

**This file is the dashboard, not the plan.** It answers *where are we, what is blocked,
and what would make us stop*. Each phase's execution checklist, ship criteria and design
now live in its own file under [`phases/`](phases/), because a single 500-line checklist
made it hard to see any one phase whole.

24 weeks, ~15 hrs/week, through Phase 4. Phases 5 and 6 roughly double that — read
[`phases/PHASE_5_REVIEW.md`](phases/PHASE_5_REVIEW.md) and
[`phases/PHASE_6_SECURITY.md`](phases/PHASE_6_SECURITY.md) before committing to either.

**Slip rule:** any phase over 150% of budget gets cut to its deterministic core. Do not
extend.

---

## Where we are

| Phase | Weeks | State | The gate that decides it |
|---|---|---|---|
| **[0 · Ingest platform](phases/PHASE_0_INGEST.md)** | 1–3 | **Shipped**, post-ship audit done (12 bugs) | 50 repos ingesting continuously ✅ |
| **[1 · Waste audit](phases/PHASE_1_WASTE_AUDIT.md)** | 4–10 | **84% — one criterion failing** | Median ≥3 findings, ≥10% recoverable ❌ *(median 2, 0.9% — one finding short)* |
| **[2 · Fix PRs](phases/PHASE_2_FIX_PRS.md)** | 11–13 | Not started | ≥5 Cadence PRs merged in repos we don't own |
| **[3 · Flaky build intelligence](phases/PHASE_3_FLAKE.md)** | 14–20 | Not started — **demand signal is weak, read the phase doc** | ≥85% flaky precision on ≥10 held-out repos |
| **[4 · Observability + trust](phases/PHASE_4_OBSERVABILITY.md)** | 21–24 | Not started | Calibration dashboard live and unattended |
| **[5 · Merge readiness + review](phases/PHASE_5_REVIEW.md)** | 14–16, 35–40 | Not started | Zero stored-key appearances anywhere |
| **[6 · Security, AI-first](phases/PHASE_6_SECURITY.md)** | 17–19, 31–46 | Not started | ≥95% precision on live-secret findings |

**The one number to watch:** Phase 1's criterion 2. It is failing, it is diagnosed, and
everything downstream assumes it passes. See
[`PHASE_1_WASTE_AUDIT.md`](phases/PHASE_1_WASTE_AUDIT.md) for the two measured causes and
what actually fixes them.

---

## What is blocking right now

Full list in [`CAVEATS.md`](CAVEATS.md) — 28 entries, 15 resolved. The ones that gate
phases rather than annoy:

| | Blocks | Status |
|---|---|---|
| **Ingest depth** — median 4 runs per workflow; only 39 of 544 streams reach `MIN_RUNS = 20` | Phase 1 criterion 2 | Worker deployed 2026-08-28; depth now accruing |
| **Only 4 of ~14 catalog rules built** | Phase 1 criterion 2 | The rules that find *large* time are the unbuilt ones |
| **Reusable-workflow mapping 18–100%** | Phase 1 critical path | Withheld below 80% rather than shown |
| **No PR → run linkage** | PR impact analysis, stacked-PR detection | Not started; one piece of work unblocks both |
| **Worker runs on a personal token** ([CAVEATS 27](CAVEATS.md)) | Any feature needing more ingest | Needs a fine-grained PAT or App token |
| **Suppression has no writer** ([CAVEATS 37](CAVEATS.md)) | Phase 2 anti-spam rule 3 | Schema ready since `001`; needs an ignore file + CLI verb |

---

## Companion documents

| Document | What it is for |
|---|---|
| [`PRODUCT.md`](PRODUCT.md) | The thesis, and §6's credibility rules — replay vs projection |
| [`CAVEATS.md`](CAVEATS.md) | Standing ledger of open findings, bugs and deliberate compromises. **Appended to after every implementation session.** |
| [`phases/PROGRESS.md`](phases/PROGRESS.md) | How far into each phase, measured against the code rather than remembered. Re-measure it; do not re-read it. |
| [`FEATURE_CANDIDATES.md`](FEATURE_CANDIDATES.md) | New feature candidates, each measured against the corpus or labelled unmeasured |
| [`phases/PHASE_2_3_CANDIDATES.md`](phases/PHASE_2_3_CANDIDATES.md) | Field research — HN, r/devops, primary GitHub sources — behind several Phase 2/3 items |
| [`cadence_product_strategy_review.md`](cadence_product_strategy_review.md) | External strategy review. Strong on positioning; see FEATURE_CANDIDATES for where its mockups conflict with §6 |
| [`EXPANSION.md`](EXPANSION.md) | Everything deliberately **not** built, with the reasoning |
| [`AUDIT_PHASE_0.md`](AUDIT_PHASE_0.md) | The 12 bugs found after Phase 0 shipped green |

---

## Continuous — from week 1

- [x] **Ingest running.** Every day of delay is a permanently lost day of history (GitHub
      retains logs 90 days). Deployed as a systemd user service 2026-08-28; see
      [`deploy/`](../deploy/).
- [ ] Eval harness written in Phase 1, reused by every later module
- [ ] Weekly publish: calibration, precision, n, date, detector SHA
- [ ] **Week 8: re-verify Kleore's feature set.** If they ship config rules + fix PRs, only
      the simulator differentiates and this plan needs revisiting.
- [ ] **Watch costops.dev and Semaphore.** costops.dev publishes our catalog as prose;
      Semaphore runs our arithmetic toward migration where we recommend repair. The audit
      report should be able to answer *"would switching beat fixing?"* rather than leaving
      it implicit. Reasoning in [`phases/PHASE_2_3_CANDIDATES.md`](phases/PHASE_2_3_CANDIDATES.md).

---

## Kill criteria

These exist to be obeyed, not admired. Each is scoped to a week so it cannot be deferred
indefinitely.

| Trigger | Action |
|---|---|
| <10% median recoverable wall time at wk 10 | Premise wrong. **Stop before Phase 2.** |
| Calibration <60% within ±25% at wk 13 | Drop dollar figures, ship raw diagnostics |
| <5 installs by wk 18 | Distribution problem. Stop building modules. |
| Flaky precision <75% at wk 20 | Deterministic core only, drop the classifier |
| Credential leak | All feature work stops until resolved and disclosed |

**On the Phase 1 trigger:** it is scoped to week 10 *with the full catalog*, so today's
failing measurement is not yet a trigger. It is the number to watch, and it must be
re-measured after the catalog is complete and ingest is deepened — not assumed to improve.

**On the Phase 3 trigger:** read it literally. Flakiness draws 15 of 1,546 HN comments and
6 of 96 r/devops comments, against 288 for cost and 229 for debuggability, plus an explicit
in-thread scope rejection. That is not a reason to cut the phase, but it is a reason to hold
its gate honestly rather than as a formality.

---

## Deferred, and explicitly rejected

**Promoted out of deferral on 2026-08-16** into Phases 5 and 6: secrets-in-CI-logs
(EXPANSION 1.1 → 6A), SCA/SBOM/reachability (PRODUCT_PLAN §8 and EXPANSION 2.5 → 6C),
static analysis orchestration (→ 6B/6C).

**Still deferred**, ranked in [`EXPANSION.md`](EXPANSION.md): preview-env cost attribution
(2wk), PR wait decomposition (2wk), test-impact advice (4wk).

**Still rejected**, with reasoning in EXPANSION Tier 3: merge queues, stacked-PR
*management*, preview-env provisioning, runner hosting, build caching, per-person velocity
metrics. Note two narrowings:

- **Stacked-PR *detection* is no longer rejected** — it moved to 5B. Only stack
  *management* stays out. See EXPANSION §3.3.
- **Workflow security scanning stays rejected** even though Phase 6 adds security. zizmor,
  poutine and StepSecurity own workflow hardening, and Harden-Runner has an agent inside
  the runner with strictly more data than our read-only position can reach. Phase 6 scans
  your **source and dependencies**, not your CI configuration.
  **Re-tested 2026-09-03** against Infisical's PAM model, which suggested flagging overbroad
  workflow `permissions:`. Still rejected: zizmor's `excessive-permissions` audit is the named
  reason in EXPANSION §3.1, our own CI already runs it, and history cannot strengthen the rule
  because we observe runs and logs but never the API calls a token made. Reasoning kept in
  [`FEATURE_CANDIDATES.md`](FEATURE_CANDIDATES.md) so it is not proposed a third time.
