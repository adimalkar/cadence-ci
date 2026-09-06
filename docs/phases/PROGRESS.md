# Phase progress — what is built, what is left

Measured 2026-09-03 against the code, not against memory. Checkbox counts come from the
phase files themselves; the "actually exists" column is the source tree and a live corpus
sweep.

The dashboard in [`../ROADMAP.md`](../ROADMAP.md) says *where we are*. This file says
*how far into each phase*, and is the thing to re-measure rather than re-read.

---

## Summary

| Phase | Checklist | Built | Left |
|---|---:|---|---|
| **[0 · Ingest](PHASE_0_INGEST.md)** | **16/16 · 100%** | Shipped, audited, deployed | Nothing. Operational caveats only |
| **[1 · Waste audit](PHASE_1_WASTE_AUDIT.md)** | **21/25 · 84%** | 8 rules, simulator, report, cost model, eval harness | 1 ship criterion failing, 2 items blocked on people |
| **[2 · Fix PRs](PHASE_2_FIX_PRS.md)** | 0/18 | Nothing | All of it. Prerequisite missing |
| **[3 · Flake](PHASE_3_FLAKE.md)** | 0/18 | Nothing | All of it. Reordered, not started |
| **[4 · Observability](PHASE_4_OBSERVABILITY.md)** | 0/24 | Nothing | All of it — **rescoped to a pillar 2026-09-05** |
| **[5 · Review](PHASE_5_REVIEW.md)** | 0/27 | Nothing | All of it. Prerequisite missing |
| **[6 · Security](PHASE_6_SECURITY.md)** | 0/25 | Nothing | All of it. 6A justified, 6B/6C behind demand |

**Everything shipped so far is Phase 0 and Phase 1.** Phases 2–6 are design documents with
no code behind them. That is the honest shape of the project: one phase finished, one
nearly finished, five specified.

---

## Phase 0 — complete

All 16 items and all 4 ship criteria are ticked. The substrate every later phase reads:
`ingest.py`, `providers/github.py`, `queue.py`, `worker.py`, `webhook.py`, `logstore.py`,
`db/`, and the six-table schema plus `workflow_blob`/`workflow_snapshot`.

**Nothing is left to build.** What remains is operational and lives in
[`../CAVEATS.md`](../CAVEATS.md): the worker is laptop-bound (25), runs on a personal token
sharing one rate limit (27), and its four large-backfill jobs hang deterministically after
exhausting that limit (34). None of these are Phase 0 checklist items; all of them stop
data arriving.

---

## Phase 1 — 84%, and the last 16% is the hard part

### Built

**Eight rules**, against a catalog originally sketched at ~14:

| Rule | Module |
|---|---|
| `no_dependency_cache` · `cache_key_never_hits` | `detectors/cache.py` |
| `no_run_cancellation` | `detectors/cancellation.py` |
| `false_needs_edge` | `detectors/serialization.py` |
| `non_discriminating_matrix_leg` | `detectors/matrix.py` |
| `irrelevant_path_trigger` | `detectors/triggers.py` |
| `long_tail_step` | `detectors/longtail.py` |
| `job_billing_rounding` | `detectors/billing.py` |

Plus the machinery: `simulate.py` (replay + projection, kept structurally apart),
`dag.py` (DAG, levelling, critical path, theoretical floor), `cost.py` (two currencies,
versioned rate card), `report.py` (self-contained HTML, replay solid vs projection
hatched), `findings.py`, `evalsweep.py`, `configstore.py`.

### The four unchecked items

| Item | Status |
|---|---|
| **Check run output** | Blocked — needs GitHub App write scope. CLI + HTML is the current surface |
| **Per-job waterfall hover** | Not built. Cosmetic relative to the rest |
| **Median ≥3 findings, ≥10% recoverable** | **Failing.** Measured below |
| **3 maintainers confirm a finding surprised them** | Blocked on contacting humans — cannot be self-verified |

### Criterion 2, measured 2026-09-03

49 of 55 corpus repos analysed:

| | 2026-08-24 | 2026-09-03 | Target |
|---|---:|---:|---:|
| Median findings | 1 | **2** | ≥3 |
| Mean findings | 1.18 | **3.02** | — |
| Max | 6 | **22** | — |
| Repos finding nothing | 22/50 | **9/49** | — |
| Median recoverable | 0.0% | **0.9%** | ≥10% |
| Repos ≥10% recoverable | 9/50 | **12/49** | — |

**Zero-finding repos falling from 22 to 9 is the real movement**, and it came from ingest
depth rather than new rules: median runs per workflow stream went from 4 to 21, and streams
reaching `MIN_RUNS = 20` from 7% to 51%, once the worker ran continuously.

**One finding short of the gate on the median.** The recoverable half is further away and is
the harder number, because it is a share of wall clock and most of the built rules recover
little of it.

### What would move it

`job_billing_rounding` was built expecting it to help. **It does not**, and that is
recorded rather than quietly dropped: standard runners are free on public repos, the corpus
is entirely public, and the detector correctly stays silent where nothing is billed. It
fires on private repos and larger runners — the paying audience, but not this corpus.

The criterion is measured on public repos, so **only rules that find wall-clock waste can
move it**. From [`../FEATURE_CANDIDATES.md`](../FEATURE_CANDIDATES.md), the two that qualify:

- **F8 · first-failing-step index** — deterministic, no ML, no gold labels, no log parsing.
  Works regardless of who pays. **Next up, and not started.**
- **F6 · scheduled-workflow waste** — cron runs on days with no commits. Pure waste, true
  on any repo.

Also unbuilt and specified: **`matrix_legs_never_independent`** ([CAVEATS 29](../CAVEATS.md)),
measured but never implemented.

---

## Phases 2–6 — specified, not started

Zero checkboxes ticked across all five. Each has a design doc worth reading before starting;
what follows is only what blocks a start.

### Phase 2 — Fix PRs

**Blocked on PR → run linkage, which does not exist.** Nothing joins a pull request to the
runs it caused, so realized-savings writeback cannot attribute correctly.

One prerequisite satisfied: config persistence landed (migration `005`, `configstore.py`),
so the round-trip ship criterion — *"200 corpus workflows, byte-identical when no fix
applied"* — is now reproducible against `load_latest()` instead of a moving HEAD.

Also settled: the render decision for `no_job_timeout` — dollars only with evidence, no
third render class.

### Phase 3 — Flake

Reordered deterministic-first, with an explicit gate before the expensive stage. Nothing
built. The demand signal is weak (15 of 1,546 HN comments, 6 of 96 on r/devops), so the
phase doc says to read its kill criterion literally rather than as a formality.

**F8 belongs to this phase's first stage** and also moves Phase 1's criterion, which makes
it unusually good value.

## Pending — picked up here next

Written 2026-09-06 so open threads survive a context loss. Ordered by what unblocks the most,
not by size. Each links to the entry holding the detail.

### Blocking a phase

| | What is left | Why it matters |
|---|---|---|
| **Recoverable criterion** | 3.46% against a ≥10% target, and it plateaus with more history ([`CAVEATS`](../CAVEATS.md) 36) | The **last thing between Phase 1 and done**. Needs rules recovering large wall-clock on the *dominant* workflow; no measured candidate does |
| **PR → run linkage** | Not started | One piece of work behind Phase 2 realized savings, Phase 5 stacked-PR findings, and F3 PR-impact |
| **Worker credential** | Shared personal token ([`CAVEATS`](../CAVEATS.md) 27) | Caps any feature needing more ingest; needs a fine-grained PAT or App token |

### Loose ends from shipped work

| | What is left |
|---|---|
| `irrelevant_path_trigger` | Fires on **0 of 51** repos at both run limits, and zero even with enrichment forced. Instrument it to report why it withholds, or delete it ([`CAVEATS`](../CAVEATS.md) 44, 45) |
| `evalsweep` enrichment | The criterion harness never calls `enrich_changed_paths`, so it measures with one rule structurally disabled ([`CAVEATS`](../CAVEATS.md) 44) |
| `non_discriminating_matrix_leg` | Revived from 0% to 3.9% by the higher run limit. `MIN_RUNS = 150` may still be too tight — never re-argued with evidence |
| Phase 4 kill criterion | Every other phase has one; Phase 4 has none, and it is now a 6-week pillar ([`CAVEATS`](../CAVEATS.md) 39) |
| Dollars-only findings | Cannot be ranked — `Savings` implies wall-clock ([`CAVEATS`](../CAVEATS.md) 30). Needs a `PRODUCT.md` §6 decision |
| Suppression surfaces | `.cadenceignore` costs one API call per audit and is fetched best-effort. No `closed_pr` writer yet — that arrives with the first fixer |

### Measured and deliberately not built

Recorded so they are not re-proposed. **`F6` scheduled-workflow waste** — 4/51 repos, median
8 minutes, top hits are maintenance bots ([`CAVEATS`](../CAVEATS.md) 43). **Overbroad workflow
`permissions:`** — zizmor owns it, and history cannot strengthen the rule
([`FEATURE_CANDIDATES`](../FEATURE_CANDIDATES.md)). **`matrix_legs_never_independent`** —
measured, never built; distinct from the shipped `non_discriminating_matrix_leg`.

### The discipline that produced this list

Three rules were built or planned on the assumption they would move Phase 1's criterion.
`job_billing_rounding` is silent on a public corpus; F6 reaches 8% of repos; F8 appeared to do
nothing until the harness was found to be reading 37% of stored history. **Measure reach on the
median repo before building**, and check the measurement before concluding about the thing
measured.

---

### Phase 4 — Observability

Nothing built, and the phase doubled on 2026-09-05 when observability was promoted from a
retention phase to a product pillar — 4 weeks to 6, 10 checklist items to 24.

It is now two halves in a fixed order. **4a export** (OTel traces, Prometheus/OTLP metrics,
findings as events) ships regardless of what gets cut; **4b surface** (flaky-cost report,
feedback decomposition, live run view, public calibration dashboard) is second and carries
the pre-decided cut order. DORA stays cut. Feedback decomposition stays reframed as a ranked
finding rather than a dashboard.

**One thing already exists**: [`webhook.py`](../../src/cadence/webhook.py) receives and
queues GitHub deliveries, which is the transport 4b.4's live run view needs. Nothing else in
this phase has any code behind it.

Growth is logged as `CAVEATS` 39 rather than absorbed — the plan's far end grew while its
near end (Phase 1 criterion 2) is still failing, and no kill criterion covers Phase 4.

### Phase 5 — Merge readiness + review

Nothing built. **Same missing PR → run linkage** blocks the stacked-PR CI findings; the
badge half (5B-a) needs only `/pulls?state=open` and could ship first.

### Phase 6 — Security

Nothing built. 6A (CI-log secrets) is justified by the substrate — it runs on logs already
stored, which nobody else has. 6B and 6C are behind explicit user demand.

---

## The three things blocking the most

1. **PR → run linkage** — one unglamorous piece of work behind three features: Phase 2's
   realized savings, Phase 5's stacked-PR findings, and PR impact analysis.
2. **A credential with its own rate limit** ([CAVEATS 27](../CAVEATS.md)) — the worker
   shares a personal token, which is why its large backfills exhaust the budget and hang.
   Nothing that needs more ingest can be built until this is fixed.
3. **One more wall-clock rule** — F8 or F6 — to move Phase 1's criterion from median 2
   to 3.
