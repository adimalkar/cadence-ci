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
| **F1** | Per-job billing rounding waste | **Measured — 7% of billed minutes, but $0 on a public corpus** | Private repos + larger runners | ✅ **Built** |
| **F2** | Matrix legs that never disagree | **Measured — 0 disagreements in 96 runs** on 2 of 8 sampled | Matrix users | Medium |
| **F3** | PR CI impact (`analyze-pr`) | Unmeasured — needs PR→run linkage | Every PR | Large |
| **F4** | CI regression detection + blame | Unmeasured — data exists | Repos with drift | Medium |
| **F5** | Required-check long pole | Unmeasured — data exists | Repos with protection | Small |
| **F6** | Scheduled-workflow waste | Unmeasured — data exists | Cron users | Small |
| **F7** | Setup tax decomposition | Unmeasured — step data exists | Every repo | Medium |
| **F8** | First-failing-step index | Unmeasured — step data exists | Every failing repo | Small |
| **F9** | Peer percentile from the corpus | Unmeasured — corpus-only moat | Every repo | Medium |
| **F10** | Artifact upload never downloaded | Unmeasured — needs log parsing | Artifact users | Medium |
| **F11** | Expired-credential failure clusters | Unmeasured — logs already stored | Repos with external auth | Small |
| **F12** | User-reachable finding suppression | **Schema exists, no writer** | Every repo — **gates Phase 2** | Small |

Candidates already carried in [`phases/PHASE_2_3_CANDIDATES.md`](phases/PHASE_2_3_CANDIDATES.md)
— `no_job_timeout`, `pipeline_fix_churn`, `cache_evicted_before_reuse`, the exit-137 split,
`platform_incident`, retry-to-green labels — are not repeated here. This document is the
set that came after it.

**F11 and F12 came from a 2026-09-03 read of [Infisical](https://infisical.com).** Most of
that product — secrets management, dynamic secrets, PKI issuance, PAM sessions, RBAC and
approval workflows — cannot be built here and should not be: it holds credentials and writes
to production, and `PRODUCT.md` §3 makes read-only-until-invited a stated principle. What
transfers is two *mechanisms*, plus one idea that turned out to be already rejected and is
recorded below so it is not proposed a third time.

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

### Correction: it is free on a public repo, and the corpus is entirely public

**Measured 2026-08-30, after the table above was written.** Standard GitHub-hosted runners
cost nothing on a public repository, and all 55 corpus repos are public. So the 1,002 hours
above were **rounded but never billed** — real arithmetic about minutes nobody paid for.

That kills a claim made elsewhere in this document: this rule does **not** move Phase 1's
failing ship criterion, because the criterion is measured on a public corpus where the rule
correctly stays silent. Verified live — `cadence audit pallets/flask` reports *"No
recoverable waste found"* even though flask's rounding fraction is 67.3%.

Where the money is real:

- **private repositories** — the paying audience, and the reason to build it
- **larger runners on public repos** — `free_on_public = false`, so billed regardless

The corpus validates the *arithmetic* but cannot demonstrate the *value*. Same shape as
CAVEATS 24, and for the same reason.

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

**Sharpened by Infisical's point-in-time recovery, 2026-09-03.** Their secrets product keeps
every version and lets you diff and roll back to any of them; the useful half of that idea is
that a *versioned config plus a metric* makes regressions self-attributing. Cadence is already
halfway there and by design — `005_workflow_config.sql` says in its own header that it exists
so *"the workflow changed here and waste started"* becomes answerable. The unbuilt half is
small: for each `workflow_snapshot` boundary on a path, compare median run duration and
failure rate in the windows either side, and surface the diff when either moves materially.

That is a **cheaper and more certain** first cut than changepoint detection, because the
candidate dates are given rather than inferred. Build it first; keep changepoint detection for
degradation with no config edit behind it, which is the harder and rarer case.
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

## F11 · Expired-credential failure clusters

Infisical's certificate product is, stripped of its issuance half, an **expiry inventory with
lead-time renewal**: it knows when a credential dies and acts before it does. The CI analogue
is a failure class nobody attributes correctly, because the symptom arrives weeks after the
cause and looks like an ordinary red build.

A token, deploy key, registry password or signing cert ages out. From that day forward a
specific step fails on every run, on every branch, for a reason that has nothing to do with
the code that triggered it. Teams re-run it, blame flakiness, and eventually someone digs.

The signature is sharp and entirely historical:

- a step that passed consistently before date *D* and fails consistently after it
- failing on **every** branch, not a subset — this is what separates it from a code regression
- the failure is a **step-level** cliff, not a gradual drift, which is what separates it from F4

We already hold every log line from every ingested run, so the corroborating evidence —
`401`, `403`, `expired`, `authentication failed` — is a grep away rather than new ingest.

> *"`docker/login-action` has failed on all 47 runs since 2026-07-14, on 6 branches. It passed
> 214 times before. Registry credentials expire; this looks like one that did."*

**Why this one is ours and not a scanner's.** No static tool can see it — the YAML is
unchanged and correct. It needs a before/after boundary in run history, which is the same
substrate argument behind every rule in Phase 1.

**State it as a hypothesis, not a diagnosis.** We cannot see the credential, only that a step
that used to authenticate stopped. Render it as *"consistent with an expired credential"* and
name the alternatives — a revoked token, a rotated secret nobody propagated, an upstream
service that changed its auth. Overclaiming here sends someone to rotate a working key.

- Detector: `expired_credential_failure` · Basis: **replay** (the failures are historical)
- Belongs in **6A**, which already reads stored logs — see
  [`phases/PHASE_6_SECURITY.md`](phases/PHASE_6_SECURITY.md)
- Guard: require ≥10 passing runs before the boundary and ≥5 failing after, on ≥2 branches

---

## F12 · Suppression a user can actually reach — *the gap that gates Phase 2*

Infisical ships three ways to silence a finding: a `.infisicalignore` file, an inline
`infisical-scan:ignore` comment, and a UI lifecycle of resolved / ignored / false-positive.
Unremarkable in their product. The reason it matters here is what a comparison turned up.

**Cadence has the schema and no way to write to it.**
[`001_initial.sql`](../src/cadence/db/migrations/001_initial.sql) defines `status`,
`suppress_scope`, `suppressed_by` and `suppressed_reason`; the `dedupe_key` comment above it
explains that waste findings key on `(rule, workflow_path, job_name)` *"so editing the YAML
does not orphan a suppression"*; and [`findings.py`](../src/cadence/findings.py) carefully
preserves a `suppressed` decision across re-audits and marks a returning finding `regressed`.

Every part of the mechanism is built except the part a user touches. Nothing anywhere sets
`status = 'suppressed'` — no ignore file, no inline comment, no CLI verb, no API.

### This is a Phase 2 prerequisite, not a nicety

[`PHASE_2_FIX_PRS.md`](phases/PHASE_2_FIX_PRS.md) anti-spam rule 3 reads: *"A closed PR
permanently suppresses that finding at `rule_repo` scope."* That rule is currently
unimplementable. A declined fix would be re-proposed on the next audit, forever — which is
precisely the behaviour that gets a bot muted, and rule 4 of the same section explains why
that damage is not recoverable.

### What to build

| Surface | Shape | Scope it sets |
|---|---|---|
| Repo file | `.cadenceignore` — one `rule_id[:workflow_path[:job_name]]` per line, `#` comments | `rule_repo` or `rule_path` |
| Inline | `# cadence:ignore <rule_id> — <reason>` in the workflow YAML | `finding` |
| CLI | `cadence suppress <finding-id> --reason …` / `cadence unsuppress` | any |

Two rules worth fixing now, while it is cheap:

1. **A reason is mandatory.** Suppressions without one become permanent mysteries; the whole
   point of the `suppressed_reason` column is to survive the person who set it.
2. **Suppression is per-rule, never global.** A blanket mute is indistinguishable from
   uninstalling, and it hides the signal that a rule is miscalibrated.

Cheap — one parser, one CLI verb, one `UPDATE` — and it converts four dormant columns into a
working feature.

---

## Considered and rejected — overbroad workflow `permissions:`

Infisical's PAM thesis is *eliminate standing access*, and the CI analogue is immediate: a
workflow granting `contents: write` when no step pushes, or omitting `permissions:` entirely
and inheriting the repository default.

**It is already rejected, twice, and the reasoning still holds.**
[`EXPANSION.md`](EXPANSION.md) §3.1 rejects workflow security scanning and names *"zizmor —
static analysis, excessive permissions"* as the tool that owns it. [`ROADMAP.md`](ROADMAP.md)
repeats the narrowing: Phase 6 scans **source and dependencies, not CI configuration**. Our
own CI runs zizmor, so we would be shipping a rule we already consume from someone better at
it.

The tempting counter-argument is that history could prove the permission is unused. It cannot:
we observe runs, jobs, steps and logs — never the API calls a token made. The honest ceiling
is *"no step in this file appears to push, publish or release"*, which is static YAML analysis
with extra steps, and zizmor already does it.

Recorded here so the idea is not re-proposed a third time.

---

## What I would build first, and why

**F8, then F6.** F1 is built — and building it corrected this section. F1 does **not** move
the criterion, because the corpus is public and the rule correctly stays silent where
nothing is billed.

That correction reframes what "moves criterion 2" means. The criterion is measured on a
public corpus, so a rule only moves it by finding **wall-clock** waste, not dollars. F1
finds dollars. F8 (first-failing-step) and F6 (scheduled-workflow waste) are true regardless
of who pays, which makes them the better candidates for the number actually blocking the
phase.

F3 is the best *product* idea in either document, and it should be built — after PR→run
linkage exists, which is a separate and unglamorous piece of work that also unblocks
stacked-PR detection.

**Then F12, and possibly before either.** F12 moves no measurement — it is not a detector —
but it is the one item here that *blocks a phase*: Phase 2's anti-spam rule 3 cannot be
implemented without it, and Phase 2 is next. It is also the cheapest thing in this document,
because three-quarters of it already exists in the schema.

**What I would not add:** anything requiring a new ingest source, until item 27 in
[`CAVEATS.md`](CAVEATS.md) is resolved and the worker has a credential with its own rate
limit. Every feature above runs on data already in the database.
