# Feature Candidates — measured against our own corpus

Companion to [`cadence_product_strategy_review.md`](cadence_product_strategy_review.md),
written in the same spirit but held to this project's own standard: **every candidate here
is either measured against the 51-repo corpus or explicitly labelled as unmeasured.**

The strategy review's central instinct is right, and it is the frame for everything below:

> **Cadence is the debugger for GitHub Actions.** It tells you why your pipeline is slow,
> expensive, or broken — proves it from your own history — predicts what a fix will change,
> and opens the PR.

What that review could not do is check whether a proposed feature would actually *find
anything*. That is the gap this document fills, and it matters more than usual right now,
because **Phase 1's ship criterion is failing at median 1 finding against a target of 3**.
A new rule that fires on two repos does not fix that. A new rule that fires on forty does.

Measurements below were taken 2026-08-30 against the live corpus: 114,778 jobs, 16,298
runs, 55 repos.

---

## Summary

| # | Candidate | Evidence | Fires on | Effort |
|---|---|---|---|---|
| **F1** | Per-job billing rounding waste | **Measured — 7% of all billed minutes** | Nearly every repo | Small |
| **F2** | Matrix legs that never disagree | **Measured — 0 disagreements in 96 runs** on 2 of 8 sampled | Matrix users | Medium |
| **F3** | PR CI impact (`analyze-pr`) | Unmeasured — needs PR→run linkage | Every PR | Large |
| **F4** | CI regression detection + blame | Unmeasured — data exists | Repos with drift | Medium |
| **F5** | Required-check long pole | Unmeasured — data exists | Repos with protection | Small |
| **F6** | Scheduled-workflow waste | Unmeasured — data exists | Cron users | Small |
| **F7** | Setup tax decomposition | Unmeasured — step data exists | Every repo | Medium |
| **F8** | First-failing-step index | Unmeasured — step data exists | Every failing repo | Small |
| **F9** | Peer percentile from the corpus | Unmeasured — corpus-only moat | Every repo | Medium |
| **F10** | Artifact upload never downloaded | Unmeasured — needs log parsing | Artifact users | Medium |

Candidates already carried in [`phases/PHASE_2_3_CANDIDATES.md`](phases/PHASE_2_3_CANDIDATES.md)
— `no_job_timeout`, `pipeline_fix_churn`, `cache_evicted_before_reuse`, the exit-137 split,
`platform_incident`, retry-to-green labels — are not repeated here. This document is the
set that came after it.

---

## F1 · Per-job billing rounding waste — *the strongest new finding*

**GitHub bills each job rounded up to the whole minute.** A 20-second job costs a minute. A
matrix of forty 30-second jobs costs forty minutes for twenty minutes of work.

Nobody reasons about this, because the receipt is one aggregate number and the waste is
distributed across thousands of tiny jobs.

### Measured, corpus-wide

| | |
|---|---|
| Jobs analysed | **114,778** |
| Actual compute | 13,340 hours |
| Billed compute | 14,342 hours |
| **Lost to rounding** | **1,002 hours — 7.0% of everything billed** |

### And it is heavily concentrated

| Repo | Jobs | Avg job | Wasted to rounding |
|---|---:|---:|---:|
| `pallets/flask` | 1,142 | 20s | **67.3%** |
| `elastic/elasticsearch` | 518 | 45s | 49.7% |
| `fastapi/fastapi` | 876 | 52s | 40.6% |
| `gin-gonic/gin` | 1,456 | 57s | 34.2% |
| `react/react` | 17,881 | 71s | 30.2% |

Flask pays for three times the compute it uses. That is not a rounding error in the
figurative sense — it is literally the rounding.

### Why this is ours

A linter cannot find it: nothing in the YAML is wrong. It requires per-job duration
history, which is exactly the substrate Phase 0 built. It is also the rare finding that is
**pure arithmetic** — no projection, no confidence interval. The minutes were billed; we
can prove it.

### The fix is real, and it has a cost

Merge trivially short jobs into their neighbours, or collapse an over-wide matrix. The
honest counter-argument, which the finding must state: fewer jobs means **less
parallelism**, so wall-clock feedback time can get *worse* while the bill gets better.
This is the first rule where the two currencies genuinely conflict, and the report has to
show both.

- Detector: `job_billing_rounding` · Basis: **replay** (solid — the minutes are historical)
- Guard: only fire when merging jobs would not extend the critical path, which the DAG
  already knows

---

## F2 · Matrix legs that have never disagreed

A matrix leg earns its minutes by failing when its siblings pass. If it never has, it is
buying redundancy nobody is spending.

### Measured

Grouping jobs by matrix base name and asking how often legs within one run reached
different conclusions:

| Matrix base | Runs | Avg legs | Runs where legs disagreed |
|---|---:|---:|---:|
| `build` | 298 | 26.1 | 31 |
| `test` | 233 | 14.3 | 28 |
| `lint` | 151 | 3.2 | 6 |
| `Ubuntu` | 96 | 3.1 | **0** |
| `Analyze` | 96 | 5.1 | **0** |

`build` disagrees often enough to justify 26 legs. `Ubuntu` and `Analyze` never once
produced a divergent outcome.

### The honest limitation, stated in the finding itself

**"Never disagreed" is not "useless."** Perfectly correlated legs in the observed window may
still catch a future regression, and a leg that has never failed may be the reason a class
of bug never shipped. This rule produces a **candidate for human judgement**, not an
instruction — closer to `preview() returns None on unfamiliar shapes` than to a fixer.

Render it as a question: *"These 5 legs have never independently caught a failure in 96
runs. Are they earning their 4m12s per run?"*

- Detector: `matrix_legs_never_independent` · Basis: **config + historical correlation**
- Never auto-fix. This one gets no `timeout.add`-style fixer.

---

## F3 · PR CI impact — the strategy review's best idea

`cadence analyze-pr` answering **"what will this PR do to CI?"** is the strongest
suggestion in the strategy review, and I would adopt it — with two corrections.

**Correction 1: it must render as a range, not a point.** The review's mockup shows
`+$183/month` and `Confidence: 91%`. A prediction about a PR that has never run is
**projection**, and `PRODUCT.md` §6 requires projection to render hatched, as a range. The
feature is right; that mockup violates our own credibility rule.

**Correction 2: it is further away than the review implies.** It needs **PR → run linkage,
which does not exist**. Config persistence landed (migration `005`), but nothing joins pull
requests to the runs they caused. That is a prerequisite, and it is the *same* prerequisite
stacked-PR detection needs — worth building once, for both.

What it should say:

```text
CI IMPACT  ·  projected, from 1,842 historical runs

  feedback time    +3m10s to +5m30s      ▨ projection
  monthly cost     +$140 to +$220        ▨ projection
  affected         test.yml, integration.yml

  why
    lockfile change invalidates a cache that hit on 83% of runs
    integration-tests moves from path-scoped to every push

  what we cannot say
    whether the new tests are slower than the ones they replace
```

That last block matters. A tool that states its own blind spots is trusted with the rest.

---

## F4 · CI regression detection and blame

Endorsed from the strategy review; the data is already there and no new ingest is needed.

CI degrades gradually — 10m → 12m → 15m → 21m — and nobody notices until developers
complain. Changepoint detection over median run duration, decomposed by step, then
attributed to the introducing commit.

**Build the detector before the blame.** "CI got 38% slower and here is the decomposition"
is useful on its own and cannot be wrong about *who*. Attribution is where precision
claims get expensive, and Phase 3 already sets a ≥70%-or-emit-nothing bar for blame. Apply
the same bar here.

Note the dependency: **config persistence makes this dramatically better.** "The workflow
changed in this window" is now an available feature, as of migration `005`.

---

## F5 · Which required check is the long pole

Branch protection decides what blocks a merge. If six required checks finish in four
minutes and the seventh takes fourteen, the seventh *is* the merge wait — the other six are
free.

```text
MERGE GATE  ·  7 required checks

  e2e            p50 11m20s   p90 14m02s   ← the gate
  test (3.12)    p50  3m10s
  … 5 more       all under 4m

  median merge wait is set by e2e alone.
  moving e2e to a post-merge job would cut it by 8m.
```

Cheap: branch protection is one API call, and run durations are held already. Directly
answers the strategy review's §6 *"why is this PR still waiting?"* for the CI portion,
without needing review-latency data.

---

## F6 · Scheduled workflows that ran with nothing to do

`schedule:` triggers fire whether or not anything changed. A nightly build on a repo with
no commits that day is pure waste, and it is invisible because it never fails.

*"Your nightly workflow ran 30 times last month. On 19 of those days the default branch had
no new commits — 6.3 hours, $23."*

Trivially detectable from `run.event = 'schedule'` joined against commit activity. The fix
is a `if: github.event.repository.pushed_at > ...` guard, or moving to `workflow_dispatch`.

---

## F7 · The setup tax

Decompose every run into **setup** (checkout, toolchain, dependency install, cache restore)
versus **work** (tests, build, lint). Then state the ratio as a finding rather than a chart:

> *"62% of your CI minutes happen before your tests start."*

The strategy review proposes this as observability (§2.2). It is stronger as a **finding**,
because it ranks against other findings and points at a specific remedy — cache, prebuilt
image, or a smaller base container.

Requires a step-name classifier. Start with a deterministic allowlist (`actions/checkout`,
`setup-*`, `npm ci`, `pip install`, `cache`) and refuse to classify what it does not
recognise, reporting coverage — the same withholding discipline the critical path already
uses below 80% mapping.

---

## F8 · First-failing-step index — Phase 3 value without a classifier

For every failed job, record the first step with a non-zero conclusion. Aggregate.

```text
WHERE YOUR BUILDS FAIL  ·  184 failures, 90 days

  pytest                  83%   ← your tests
  npm ci                   9%   ← infrastructure, not you
  actions/checkout         5%   ← infrastructure, not you
  docker build             3%
```

Deterministic, no ML, no log parsing, no gold labels. It answers *"why did CI fail?"* at
the level people actually ask it, and it splits **your code** from **the platform** — which
is what the exit-137 finding in `PHASE_2_3_CANDIDATES.md` showed people get wrong.

This is the concrete form of the strategy review's "make flake intelligence deterministic
first," and it should ship **before** the taxonomy, the clustering, or the classifier.

---

## F9 · Peer percentile — the only feature that needs the corpus

Everything else here works on one repo. This one cannot exist without the 51-repo dataset,
which makes it the most defensible thing in the document.

> *"Your `npm ci` step averages 4m12s. Across 38 comparable Node repos in our corpus, the
> median is 1m50s. You are in the slowest decile."*

A single-repo tool can say "this step is slow" only by guessing a threshold. We can say it
by comparison, and comparison is what makes a maintainer act.

**Constraints, which are not optional:** public repositories only; cohort by
ecosystem and size or the comparison is meaningless; publish the cohort size with every
claim; never name another repo in a customer-facing report. Get this wrong and it is a
privacy incident rather than a feature.

---

## F10 · Artifacts uploaded and never downloaded

`upload-artifact` costs minutes and storage. If no later job or workflow downloads it, and
nobody fetched it from the UI, it is write-only.

Detectable by pairing upload steps against download steps across the run graph. Weaker
evidence than the rest — a human may have downloaded it from the web UI, which we cannot
see — so this must be phrased as *"no workflow downloads this"*, never *"nobody uses this"*.

---

## What I would build first, and why

**F1 and F8, in that order.** Both are small, both are deterministic, both fire on almost
every repo, and neither needs a prerequisite that does not exist.

That last point is the one that decides it. Phase 1's ship criterion fails at **median 1
finding**, and the diagnosis in `ROADMAP.md` is that the rules which find *large* time are
still unbuilt. F1 alone would fire on nearly every repo in the corpus, with replay-grade
evidence and no projection. It moves the number that is currently blocking the phase.

F3 is the best *product* idea in either document, and it should be built — after PR→run
linkage exists, which is a separate and unglamorous piece of work that also unblocks
stacked-PR detection.

**What I would not add:** anything requiring a new ingest source, until item 27 in
[`CAVEATS.md`](CAVEATS.md) is resolved and the worker has a credential with its own rate
limit. Every feature above runs on data already in the database.
