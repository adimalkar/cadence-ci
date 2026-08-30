# Cadence — Product Strategy & Roadmap Review

## Executive Takeaway

Cadence has a stronger product opportunity than simply becoming a **CI optimization dashboard** or an **AI code-review bot**.

The strongest positioning is:

> **Cadence is the intelligence layer that explains why a GitHub PR/CI pipeline is slow, expensive, unreliable, or unsafe — with evidence — and then proposes or applies the smallest safe fix.**

The core product loop should be:

**Observe → Explain → Quantify → Predict → Fix → Verify**

The roadmap is strongest through the historical CI analysis, simulation, fix PR, and verification work. Later phases branch into too many adjacent products, especially broad security and engineering analytics.

The recommended product thesis is:

> **Keep GitHub. Make the painful parts of GitHub Actions dramatically better.**

---

# 1. What Is Already Excellent

## 1.1 Historical evidence instead of static YAML analysis

This is one of Cadence's strongest differentiators.

Instead of merely saying:

> "You may want to remove this `needs:` dependency."

Cadence can say:

> "Your `needs:` edge is unnecessary and removing it would have saved approximately X minutes/run across the last N runs."

That changes the product from a linter into an evidence-backed engineering tool.

The principle to preserve is:

> **Evidence or it doesn't ship.**

---

## 1.2 Replay / simulation

The simulator can become a major competitive advantage.

Instead of:

> "Your workflow is inefficient."

Cadence can show:

> "Here is what your historical runs would have looked like if this change had existed."

This creates a useful sequence:

**Current state → Counterfactual → Expected savings → Confidence**

That is much stronger than generic CI analytics.

---

## 1.3 Fix PRs instead of recommendations

The progression in the roadmap is strong:

**Finding → Explanation → Simulation → Patch → PR → Measure actual outcome**

This turns Cadence from a reporting tool into an engineering tool.

The Phase 2 roadmap already captures this direction with comment-preserving YAML edits, fixers, opt-in write scopes, anti-spam rules, and realized-savings writeback. fileciteturn0file0L202-L214

---

## 1.4 Evidence requirements and calibrated output

Cadence's insistence on evidence, confidence, and separating replayed historical results from projections is excellent.

The roadmap explicitly distinguishes replay from projection in the report, which is important for credibility. fileciteturn0file0L146-L158

This matters because developers will distrust AI-generated CI recommendations if they cannot verify where the claim came from.

---

# 2. What Should Be Cut or Deferred

## 2.1 DORA metrics

The roadmap includes:

> DORA — one page, four queries

This is the easiest feature to cut.

Why:

- It is already a crowded category.
- It does not strengthen Cadence's GitHub Actions-specific wedge.
- It risks making Cadence feel like another engineering analytics dashboard.

The goal should be to make users think:

> **"Cadence understands my GitHub Actions pipeline."**

Not:

> "Cadence is another engineering metrics platform."

**Recommendation: Cut unless customers explicitly demand it.**

---

## 2.2 General observability

The feedback decomposition in the roadmap is useful:

**push → queue → execution → post**

Keep this only insofar as it answers:

> **Why is my PR taking so long?**

Avoid turning Cadence into a broad observability platform.

A useful experience would be:

| Stage | Time |
|---|---:|
| Queue | 3m 12s |
| Setup | 2m 41s |
| Tests | 9m 34s |
| Artifact/upload | 1m 22s |
| Post-processing | 1m 52s |

Then explain:

> **Only 9m34s is actual test execution; 8m59s is pipeline overhead.**

That is useful. A large general-purpose dashboard suite is not.

---

## 2.3 Reconsider the large ML-heavy flaky build phase

The roadmap currently dedicates substantial effort to:

- Flaky-build taxonomy
- Log normalization
- Signature clustering
- Gradient-boosted classifier
- Calibration
- Blame candidates

The research already included in the roadmap shows a comparatively weak demand signal for flakiness versus cost and debuggability. fileciteturn0file0L306-L322

The recommendation is **not to eliminate flaky-build intelligence**, but to make the first version deterministic.

For example:

> Same commit → failure → rerun → success
>
> 11 failures across 184 runs
>
> 9 recovered on retry
>
> **Probable flaky failure: 82%**
>
> **Reruns cost 3h14m this month**

This delivers substantial value before requiring a classifier.

ML can be introduced later when the gold-label dataset justifies it.

---

## 2.4 Broad security platform work

Phase 6 expands into:

- CI-log secrets
- Notebook scanning
- AI security rules
- MITRE ATLAS
- SCA
- SBOM
- Reachability
- Sandboxing

Together these begin to resemble a standalone security platform.

That creates a serious product-scope problem because security is already a large and competitive category.

The roadmap itself recognizes existing ownership in workflow security from tools such as zizmor, poutine, and StepSecurity. fileciteturn0file0L498-L511

### What to keep

**CI-log secret detection** is a natural extension because Cadence already ingests and stores CI logs.

Example:

> "An AWS credential appeared in 7 historical CI runs."

That is directly connected to Cadence's core substrate.

### What to defer

Most of the generalized security platform work should remain out of the core roadmap unless user demand proves otherwise.

---

# 3. Reposition the AI Review Feature

The BYOK review concept is interesting, but Cadence should not try to compete head-on with generic AI code-review tools.

The better category is:

# CI-Aware Code Review

Generic AI review asks:

> "Is this code correct or maintainable?"

Cadence should ask:

> **"What will this code change do to CI?"**

Examples:

> "This dependency change increases CI execution time by ~18%."

> "This workflow change adds 12 matrix legs."

> "This Dockerfile change invalidates a cache used by 83% of historical builds."

> "This PR causes integration tests to run on every push instead of only on relevant paths."

That is a differentiated review surface because it combines the PR diff with Cadence's historical execution knowledge.

---

# 4. Most Important Feature to Add: PR Impact Analysis

This is probably the biggest missing feature in the current roadmap.

Add a command / GitHub check concept such as:

`cadence analyze-pr`

The question it answers is:

> **What will this PR do to CI?**

Example output:

```text
CI IMPACT

Estimated feedback time: +4m 18s
Estimated monthly cost: +$183
Affected workflows:
  - test.yml
  - build.yml
  - integration.yml

Reasons:
  - dependency lockfile invalidates cache
  - 3 new matrix legs
  - integration tests now run on every push

Confidence: 91%
```

This creates a natural reason for Cadence to live directly inside GitHub pull requests.

---

# 5. Another High-Value Feature: "What Changed in CI?"

When a PR modifies `.github/workflows/*`, Cadence should automatically explain the resulting CI behavior.

Example:

```text
CI IMPACT

+23% expected execution time
+$94/month projected cost
+1m 42s median PR feedback

Main cause:
  integration-tests changed from
  path-scoped execution to every push.

Evidence:
  1,842 historical runs
```

This is a natural extension of the existing historical analysis and simulation engine.

No LLM is required to establish the underlying claim.

---

# 6. Important Missing Feature: "Why Is This PR Still Waiting?"

Combine PR analysis with the feedback-loop decomposition.

Example:

```text
MERGE READINESS

72% — waiting on CI

3m 41s   runner queue
8m 02s   tests
4m 12s   required review
2m 17s   other blockers

Expected merge delay: 18–26 min

Primary cause:
  integration-tests account for
  61% of historical CI delay.
```

This gives Cadence a direct connection to one of the most painful developer experiences around pull requests: waiting for feedback.

---

# 7. Another Strong Feature: CI Regression Detection

Slow CI often becomes slower gradually:

**10m → 12m → 15m → 21m → 26m**

Teams frequently notice only after developers have already started complaining.

Cadence should say:

> **CI became 38% slower over the last three weeks.**

Then decompose the increase:

```text
+4m 12s  dependency install
+3m 04s  test suite
+1m 53s  artifact upload
+0m 41s  queue
```

Then identify the introducing commit or PR.

---

# 8. CI Blame

A particularly useful capability is answering:

> **Who or what introduced this CI regression?**

Example:

```text
CI regression detected

Median duration:
  11m → 19m

Introduced by:
  a82f91c

PR:
  Enable integration tests for API changes

Impact:
  +7m 42s

Confidence:
  94%
```

Developers don't merely want to know that CI is slower. They want to know **what caused the regression**.

This fits the historical architecture extremely well.

---

# 9. GitHub's Problems Cadence Can Realistically Address

Cadence should not try to convince teams that GitHub itself is a bad platform.

A stronger message is:

> **Keep GitHub. Make its painful workflows better.**

The recurring problem areas relevant to Cadence include:

## 9.1 Slow CI feedback loops

Classic loop:

**push → wait → failure → fix → push → wait again**

Cadence can attack the waiting portion by identifying unnecessary work, queue time, bad workflow structure, and regressions.

## 9.2 Runner queuing and infrastructure delays

Teams can experience jobs sitting in queue for substantial periods. Cadence can distinguish queue time from actual execution time so users know whether the problem is their workflow or runner capacity.

## 9.3 CI cost

Actions pricing and runner economics can be difficult to reason about across workflows and runner types.

Cadence's advantage is that it can translate execution behavior into:

**minutes → cost → waste → proposed fix → realized savings.**

## 9.4 Debuggability

A major frustration is not merely that CI fails, but that developers cannot quickly answer:

> **Why did it fail?**

Cadence should focus on evidence-backed explanations rather than generic AI guesses.

## 9.5 Configuration complexity

GitHub Actions workflows become complex through:

- `needs:` relationships
- matrices
- reusable workflows
- cache behavior
- path filters
- concurrency
- conditional execution
- job dependencies

Cadence is well positioned because it can understand both configuration and actual execution history.

---

# 10. Don't Replace GitHub — Diagnose Whether to Fix or Migrate

One of the most interesting ideas already present in the roadmap is:

> **"Would switching beat fixing?"**

Keep this concept and make it explicit.

Cadence can eventually compare:

```text
Current GitHub Actions
23m 14s

Optimized GitHub Actions
14m 03s

Alternative CI platform estimate
12m 51s

Migration complexity
High

Estimated savings
$430/month

Recommendation
Stay on GitHub
```

Or:

```text
Recommendation
Consider migration.

Even after optimization,
GitHub Actions retains significant
infrastructure overhead.
```

The value is credibility: Cadence is not financially incentivized to claim that every GitHub problem can be fixed.

The roadmap already points toward making the audit report capable of answering this question. fileciteturn0file0L461-L471

---

# 11. Stacked PR Detection: Keep It Narrow

The roadmap's current decision here is good.

Keep:

- Stacked PR detection
- Stack visualization
- CI dependency implications
- Rebase churn / CI re-run impact

Do **not** build:

- A stacked-PR CLI
- Stack management
- Branch-management workflows
- Merge queues

The roadmap correctly narrowed the feature to detection and CI consequences rather than competing with dedicated stacked-PR workflow products. fileciteturn0file0L351-L372

---

# 12. Preserve the Silence Philosophy

One of the best principles in the roadmap is:

> **Hunks with no Cadence evidence are omitted from the prompt entirely.** fileciteturn0file0L375-L392

This should become a product principle.

A strong framing is:

> **Cadence doesn't review everything. It reviews what it has evidence to review.**

This is a useful answer to AI-review fatigue and noisy code-review bots.

---

# 13. Your Moat Is Not the LLM

Do not build Cadence's identity around BYOK or a specific model provider.

The LLM should be replaceable.

The moat is:

**Historical CI dataset**

+

**Execution graph**

+

**Workflow semantics**

+

**Counterfactual simulator**

+

**Verified outcomes**

The architecture should look like:

```text
Deterministic engine
        ↓
Finds problem
        ↓
Historical evidence
        ↓
Proves problem
        ↓
Simulator
        ↓
Predicts impact
        ↓
LLM
        ↓
Explains problem
        ↓
Patch engine
        ↓
Creates fix
        ↓
GitHub PR
        ↓
Future runs
        ↓
Verify prediction
```

That creates a feedback loop where the product can measure how accurate its recommendations actually were.

---

# 14. Recommended Roadmap Structure

The current roadmap is organized heavily around feature categories. A stronger roadmap is organized around one user journey.

## Phase 0 — Observe

Already mostly complete.

- GitHub ingestion
- Workflow history
- Logs
- DAG reconstruction
- Step timing
- Cost model

## Phase 1 — Explain

Answer:

- Why is CI slow?
- Why is CI expensive?
- Why does CI fail?
- Where is recoverable waste?

## Phase 2 — Predict

Build the differentiated layer:

- PR Impact Analysis
- CI Regression Detection
- CI Blame
- Counterfactual simulation

## Phase 3 — Fix

Automate remediation:

**Finding → patch → PR**

## Phase 4 — Verify

After merge:

```text
Predicted: -4m 20s
Actual:    -4m 31s
```

Use the outcome to improve calibration and trust.

## Phase 5 — CI-Aware Review

Only now introduce BYOK LLM review.

Review the PR using:

- Diff
- Workflow changes
- CI history
- Cost impact
- Runtime impact
- Reliability impact
- Cadence evidence

## Phase 6 — Security Adjacent

Keep security tightly related to the existing CI substrate, especially:

- CI-log secret detection
- Dependency impact
- CI-related risk detection

Avoid becoming a general security platform unless user demand clearly pulls the product there.

---

# 15. Revised Feature Priority

| Priority | Feature | Recommendation |
|---|---|---|
| P0 | Historical CI ingest | Keep |
| P0 | Waste / cost detection | Keep |
| P0 | Replay / counterfactual simulation | **Double down** |
| P0 | Fix PRs | **Double down** |
| P0 | PR Impact Analysis | **Add** |
| P0 | CI Regression Detection | **Add** |
| P0 | CI Blame | **Add** |
| P1 | Queue / feedback decomposition | Keep |
| P1 | Flake detection | Keep, deterministic first |
| P1 | Stacked PR detection | Keep |
| P1 | CI-aware BYOK review | **Strong addition** |
| P2 | CI-log secret scanning | Keep |
| P2 | GitHub vs migration analysis | **Very interesting** |
| P2 | Public calibration | Keep |
| P3 | DORA | Cut |
| P3 | Broad SCA / SBOM | Cut or defer |
| P3 | Reachability | Cut or defer |
| P3 | General AI security platform | Cut |
| P3 | Sandbox | Cut |
| P3 | Full stacked PR management | Cut |
| P3 | Runner hosting | Cut |
| P3 | Merge queue | Cut |

---

# 16. Positioning Options

## Option A

**Cadence — Make GitHub Actions faster, cheaper, and easier to debug.**

## Option B

**Cadence — Find out why your CI is slow, expensive, or broken.**

## Option C

**Cadence — The intelligence layer for GitHub Actions.**

### Recommended

**Option B** is the clearest problem-first positioning.

Then use a secondary line:

> **Cadence analyzes your GitHub Actions history, proves problems with real execution data, simulates fixes, and opens the PR to apply them.**

---

# 17. Strongest Long-Term Product Thesis

The most compelling version of Cadence is not:

> **AI code review + CI management**

It is:

# The debugger for GitHub Actions

Cadence should answer:

**Why was this PR slow?**

**Why did CI fail?**

**Why is CI expensive?**

**Why did CI get slower?**

**What changed?**

**What will this PR do to CI?**

**How do I fix it?**

**Did the fix actually work?**

That creates one coherent identity across the strongest parts of the roadmap.

---

# 18. Recommended Product Loop

```text
                    GitHub PR
                        │
                        ▼
               ┌────────────────┐
               │ Cadence sees   │
               │ the change     │
               └───────┬────────┘
                       │
            ┌──────────┼──────────┐
            ▼          ▼          ▼
        CI impact   Risk change  Cost change
            │          │          │
            └──────────┼──────────┘
                       ▼
              Historical evidence
                       │
                       ▼
                Counterfactual
                  simulation
                       │
                       ▼
                Recommended fix
                       │
                       ▼
                    Fix PR
                       │
                       ▼
                     Merge
                       │
                       ▼
              Verify prediction
                       │
                       ▼
                Improve confidence
```

This is the product loop worth optimizing the entire roadmap around.

---

# 19. Bottom Line

The strongest version of Cadence is a **GitHub Actions intelligence and remediation layer**, not a generic DevOps dashboard, AI code-review bot, or security platform.

The areas to lean into hardest are:

1. **Evidence-backed CI diagnosis**
2. **Counterfactual simulation**
3. **PR CI Impact Analysis**
4. **CI regression detection and blame**
5. **Automatic fix PRs**
6. **Verification of predicted vs actual outcomes**
7. **CI-aware BYOK review**

The areas to aggressively defer or cut are:

- DORA
- Broad observability
- Large ML investment before deterministic flake intelligence proves demand
- Full SCA/SBOM/reachability platform
- General AI security platform
- Sandbox infrastructure
- Merge queues and stacked-PR management
- Runner hosting

The key strategic principle should be:

> **Don't replace GitHub. Make GitHub's worst developer experience dramatically better.**

And the most promising product sentence is:

> **Cadence tells you why your GitHub Actions pipeline is slow, expensive, or broken — proves it from your own history, predicts what a fix will change, and opens the PR to fix it.**
