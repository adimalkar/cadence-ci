# Phase 3 — Flaky Build Intelligence

**Weeks 14–20 · Catalog class F · The deepest moat, built on 4 months of history**

---

## What this phase delivers

Failure clustering, a calibrated flaky classifier, blame candidates, and — the part most
tools miss — a **build-level** flake taxonomy that covers infrastructure flake, not just
test flake.

By week 14 the database holds four months of continuously ingested history from the
50-repo corpus. The data-collection tail that made this a 6–7 week slog when it was Phase 1
has already elapsed in the background. This is the single biggest structural payoff of the
reordering.

---

## The finding that reshapes this phase

The most useful research result for Cadence is a 2026 study of flaky builds in GitHub
Actions across 1,960 Java projects
([arXiv:2602.02307](https://arxiv.org/html/2602.02307v1)):

- **3.2% of builds are rerun**, and **67.7% of rerun builds show flaky behaviour.**
- Across the dataset, reruns burned ~339 years of wall-clock waiting and ~31.6 years of
  compute.
- 15 distinct failure categories were identified. Critically:

| Cause | Share |
|---|---|
| Flaky tests | 65% |
| **Network issues** | **15.8%** |
| **Dependency resolution** | **6.32%** |
| **External environment inconsistency** | **4.7%** |
| Rate limiting, concurrency, compilation, OOM | remainder |

**Roughly 35% of flaky builds are not flaky tests at all.** They are network timeouts,
registry failures, rate limits, and OOM kills.

Every flaky-test product on the market — Kleore, BuildPulse, Datadog, Trunk — is scoped to
the 65%. A maintainer whose CI is red twice a week from npm registry timeouts gets nothing
from any of them, because their unit of analysis is the test.

**Our unit of analysis is the build.** That is a real, evidence-backed differentiator that
costs us nothing extra: we already ingest full logs for infrastructure reasons, and
infra-flake signatures are *easier* to detect than test flake, not harder.

Second-order value: infra flake usually has a **config fix** (retry with backoff, registry
mirror, pinned base image, larger runner), which means it feeds Phase 2's fixer pipeline.
Test flake usually needs a human to fix the test.

---

## Scope

### F1 — Build-level flake taxonomy (new, weeks 14–15)

Deterministic classifiers over normalized log signatures. Ship these first — they need no
ML, they cover 35% of the problem, and several produce actionable fixes.

| Category | Signature markers | Fix |
|---|---|---|
| Network / timeout | connection reset, ETIMEDOUT, DNS failure, TLS handshake | retry + backoff |
| Registry / dependency resolution | npm/pypi/maven 5xx, checksum mismatch, lockfile drift | mirror, retry, pin |
| Rate limiting | 429, `API rate limit exceeded`, secondary limit | token rotation, backoff |
| OOM | OOMKilled, exit 137, JVM heap exhaustion | larger runner |
| Concurrency / port | address already in use, lock contention | isolation, port randomization |
| External service | third-party API 5xx in test setup | mock or mark |

Each is a rule with a golden-corpus fixture set, exactly like Phase 1's catalog. This is
the same machinery, pointed at failure logs instead of timing data.

### F2 — Log normalization and clustering (weeks 16–17)

Pipeline detailed in `PRODUCT_PLAN.md` §6.1–6.2. Unchanged and still correct:

1. Strip ANSI, retain byte offsets (needed for `log_span` evidence).
2. Locate the failure region.
3. Normalize — timestamps, durations, hex/UUIDs, absolute paths (keep repo-relative tail),
   ports, PIDs, memory addresses, IPs, temp dirs, line numbers **in vendor paths only**.
4. Signature = normalized assertion line + exception type + top 3 first-party frames.
5. Hash.

Framework extractors for pytest, jest/vitest, go test, JUnit/Maven, cargo, RSpec. Generic
fallback tagged `low_confidence`.

**Test it like a compiler.** ~300 real failure logs → expected signature, run on every
normalization change. Without this, clustering regresses silently and permanently.

Clustering: exact hash first, then near-duplicate merge via MinHash / normalized edit
distance over the signature token stream.

### F3 — Flaky classifier (weeks 18–19)

Label sources, strongest to weakest — unchanged from `PRODUCT_PLAN.md` §6.3:

1. Same tree SHA, different outcome — near-certain, rare, the gold set.
2. Retry succeeded with no intervening commit (needs `run_attempt`, captured since week 1).
3. Failed on PR, same test green on base at the same time.
4. Cross-repo prior: signature flaky in ≥3 other repos.

The published 3.2%-rerun figure lets us **size the gold set before building anything**: the
corpus needs roughly 30 reruns per 1,000 builds, of which ~68% are flaky. Sample the
database at week 14 and check the real number against it. If the corpus yields under a few
hundred gold labels, extend ingest before training rather than training on noise.

Model: **gradient-boosted trees** (LightGBM/XGBoost). Tabular, small, explainable, trains
in seconds, and feature importances answer "why do you think this is flaky" — which we will
be asked constantly.

**Calibrate** with isotonic regression or Platt scaling on held-out data, so
`confidence: 0.85` means 85% of such findings are correct. An uncalibrated score is a lie
repeated in the UI.

### F4 — Blame candidates (week 20)

Deterministic: intersect files in the failing stack trace with files changed in the
PR/commit range. Rank by direct trace appearance, then `git log -L` recency on the specific
lines, then churn. Emit ≤3 with confidence, **or emit nothing.** A blame guess that is
wrong twice destroys trust in the whole module.

---

## Prior art

### Academic and OSS

- **[arXiv:2602.02307](https://arxiv.org/html/2602.02307v1)** — the taxonomy above, plus
  **FlakeDetector**: sentence-transformer log vectorization + structured features
  (developer experience, code complexity, project stability), improving F1 by up to 20.3%
  over baselines. **Two datasets released at `flaky-build.github.io`** — 1,960 Java
  projects at scale, plus a curated flaky-failure set from 10 projects.
- **[WithSecureOpenSource/flaky-tests-detection](https://github.com/WithSecureOpenSource/flaky-tests-detection)**
  — GitHub Action + Python package; processes historical xunit results and flags tests that
  change state most often. Simple, deployed, a good baseline to beat.
- **[srivastava-rajeev/flaky-test-prediction-ml](https://github.com/srivastava-rajeev/flaky-test-prediction-ml)**
  — statistical features from historical runs, scikit-learn + XGBoost. Confirms our model
  choice is the conventional one.
- **[alfcan/crossproject-flaky-test-prediction](https://github.com/alfcan/crossproject-flaky-test-prediction)**
  — cross-project prediction, i.e. the academic form of our cross-repo prior.
- **flake-it**, **IDoFT**, **FlakeFlagger**, NonDex/iDFlakies — order-dependent and
  implementation-dependent flake, detected by *re-execution*. Different technique
  (rerun-based, expensive) but a useful labelled dataset source.

**Use the released datasets to bootstrap.** Training the first classifier on published
labelled data before our own gold set matures is weeks of calendar time saved, and it gives
an external baseline to report against.

### Commercial

Kleore, BuildPulse, Datadog Test Optimization, Trunk Flaky Tests, CircleCI test insights,
Buildkite Test Engine. All test-scoped. All require an install or test-runner
instrumentation.

---

## One reconsidered decision: embeddings

`PRODUCT_PLAN.md` §6.2 says "do not use embeddings here." That remains right **for
clustering** — deterministic hashing is cheap, explainable, and not the bottleneck.

It is worth re-examining **for classification**, where the paper reports a material F1 gain
from sentence-transformer log vectorization. These are different problems: clustering needs
stable identity across runs, classification needs generalization across phrasings.

The rule that must not bend is `PRODUCT.md` §2 rule 2 — the model is never the *decider*.
An embedding used as one feature among many in a GBT, whose output is calibrated and
explainable, does not violate that. An LLM asked "is this flaky?" does.

Decide empirically at week 18: if embeddings-as-features beat the tabular baseline by more
than a few points on held-out repos, take the gain and document the reasoning. Otherwise
stay tabular. A local sentence-transformer adds no API cost and no latency to the detection
path.

---

## Ship criteria

1. Golden corpus green: 300 logs → expected signatures.
2. Build-level taxonomy (F1) covers **≥80% of non-test flaky failures** in the corpus.
3. Flaky classification **≥85% precision on ≥10 held-out repos.**
4. Calibration: predicted confidence within ±10% of observed accuracy per decile.
5. Blame candidates: **≥70% precision, or the module emits nothing.**
6. Flaky cost is expressed in the two currencies from Phase 1.

---

## Risks

**The gold set may be too small.** 3.2% rerun rate on a 50-repo corpus may not yield enough
same-tree-different-outcome pairs. Mitigations: the published datasets, and extending ingest
breadth rather than compromising the labelling function. **Check this at week 14, before
building anything** — it is a cheap query and it determines whether F3 is viable.

**Precision below 75% at week 20** → ship the deterministic core only (unchanged-tree
failures, retry cost, F1 taxonomy) and drop the classifier. This is still a good product,
and F1 alone covers a third of the problem no competitor addresses.

**Java-centric evidence.** The 1,960-project study is Java. The taxonomy shares are
plausibly language-dependent — a JS corpus likely skews harder toward registry and network
flake. Treat the percentages as directional, and re-measure on our own corpus before
quoting them publicly.

---

# Execution checklist

Moved from `ROADMAP.md` 2026-08-30.

## Read this before committing seven weeks

Flakiness draws **15 of 1,546 HN comments and 6 of 96 r/devops comments**, against 288 for
cost and 229 for debuggability — plus an explicit in-thread scope rejection: *"Flaky tests…
A Dev team problem, not CI/CD."*

Three readings, none conclusive: sampling bias (those threads were framed around pricing,
and the literature says ~59% of developers hit flakiness monthly); flakiness is suffered
privately as a re-run click while cost arrives as a bill someone defends in public; or this
phase really is further from felt pain than Phases 1–2.

**This is not a reason to cut the phase.** It is a reason to read its kill criterion
literally, and to sequence the deterministic work first so that value lands before the
expensive work starts.

## Build order — deterministic first, classifier last

The single most useful change to this phase is reordering it. Everything in stage 1 ships
value without a model, gold labels, or log parsing.

### Stage 1 — deterministic, no ML

- [ ] **Week 14 first task: query the corpus for gold-label count.** Expect ~30 reruns per
      1,000 builds, ~68% flaky. If under a few hundred, extend ingest before training.
- [ ] **Retry-to-green as the bootstrap label.** A same-commit re-run flipping fail → pass
      is the strongest flakiness signal obtainable **without instrumenting anyone's test
      framework**. The Phase 0 audit already restored the earlier-attempt ingest this
      depends on, so it costs nothing and gives week 14 a second source to cross-check
      against.
- [ ] **First-failing-step index** (`FEATURE_CANDIDATES.md` F8). For every failed job,
      record the first step with a non-zero conclusion, and aggregate:

      ```text
      WHERE YOUR BUILDS FAIL · 184 failures, 90 days
        pytest             83%   ← your tests
        npm ci              9%   ← infrastructure, not you
        actions/checkout    5%   ← infrastructure, not you
      ```

      No ML, no gold labels, no log parsing. It answers *"why did CI fail?"* at the level
      people ask it, and it separates **your code** from **the platform** — which is the
      distinction the exit-137 finding shows people get wrong.
- [ ] **Deterministic flake summary.** *"11 failures across 184 runs, 9 recovered on retry
      → probable flaky 82%. Reruns cost 3h14m this month."* Ship this before any classifier.

### Stage 2 — taxonomy

- [ ] F1 build-level taxonomy: network · registry · rate-limit · OOM · concurrency ·
      external service (weeks 14–15)
- [ ] **Split OOM into guest-OOM vs host-eviction.** Exit 137 is two different failures
      sharing one code: the kernel OOM killer ("your build needs more memory") and a
      host-level SIGKILL where the container's own diagnostics show no pressure at all —
      ~500 MB used, 1.8 GB free, nothing in `dmesg`. Opposite remediation; classing both as
      OOM sends developers hunting memory for an infrastructure problem.
- [ ] **`platform_incident` class.** GitHub is the external service that matters most, and
      the only one attributable from an authoritative third-party record rather than
      inferred from logs: the public GitHub Status Atom feed. Failures inside a declared
      Actions incident window should be **excluded from flake statistics entirely** rather
      than diluting precision. Cheap, and it was the highest-scoring comment in the
      r/devops thread by roughly 2×.

### Stage 3 — the expensive half, gated on stage 1 proving demand

- [ ] F2 log normalization + signature + clustering (16–17)
- [ ] **Golden corpus: 300 real failure logs → expected signature**
- [ ] F3 classifier: GBT, calibrated (isotonic/Platt) (18–19)
- [ ] Bootstrap from published datasets (`flaky-build.github.io`) before own gold set matures
- [ ] Week 18 decision: embeddings-as-features only if they beat tabular on held-out repos
- [ ] F4 blame candidates — ≤3 or nothing (20)

**Gate between stages.** If stage 1 ships and nobody engages with the flake output, stage 3
is seven weeks aimed at a problem this audience is not asking to have solved. Check before
spending it.

## Ship criteria

- [ ] Golden corpus green
- [ ] F1 covers ≥80% of non-test flaky failures
- [ ] ≥85% flaky precision on ≥10 held-out repos
- [ ] Calibration within ±10% per decile
- [ ] Blame ≥70% precision, or emits nothing

## Adjacent work this phase should claim

**CI regression detection and blame** (`FEATURE_CANDIDATES.md` F4) is a better fit here
than in Phase 4. It uses the same historical substrate and the same "attribute a failure to
a cause" machinery, and config persistence (migration `005`) just made *"the workflow
changed in this window"* an available feature.

Build the detector before the blame: *"CI got 38% slower, and here is the decomposition"*
is useful on its own and cannot be wrong about **who**. Hold attribution to the same
≥70%-or-emit-nothing bar as F4 blame candidates.
