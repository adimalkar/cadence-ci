# Expansion Surface — Researched Candidates, Ranked

**What else could Cadence do, what already exists, and what is actually worth building.**

---

## The premise, examined

"More features → better adoption" is the intuition behind this document, and it is worth
stating plainly that the evidence runs the other way in this specific category.

The PR-bot market — CodeRabbit, Qodo, Greptile — competes almost entirely on **noise**.
Every one of them can produce more findings than a user wants; the differentiator is
restraint. The universal complaint about that category is not "too few features," it is
"too many comments." Our own kill criteria already encode this: *three modules at 90% beat
six at 60%.*

Features help adoption when each one **works, is trusted, and reuses data we already
hold.** A feature that shares no substrate with the core costs a full build, dilutes the
pitch, and adds a surface that can be wrong.

So the ranking criterion here is not "is this a good idea." It is:

> **(adoption value) × (probability it works well) ÷ (weeks of work), given that we already
> have step-level CI history.**

Everything below is real and researched. Tier 1 is what I'd build. Tier 3 is what I'd
deliberately not, and knowing why is as useful as the list itself.

---

## Tier 1 — Build these (high value, reuses our data, unserved)

### 1.1 Secret exposure in CI logs

**Weeks: 2 · Confidence: high · Novelty: high**

We already store every log line from every run. Scanning that archive for leaked
credentials is nearly free, and the analysis is *historical* — which is exactly what no
other secret scanner does.

Every existing scanner (GitGuardian, TruffleHog, Gitleaks, GitHub secret scanning) looks
at **source code and git history**. None look at **CI log output**, where secrets leak
constantly and invisibly: an echoed env var, a debug flag printing a full request header, a
crash dump containing a connection string, `set -x` in a script with a token argument.

Those logs are retained for 90 days and are readable by anyone with repo read access. On a
public repo they are readable by *everyone*.

Do it properly: entropy + pattern + **verified liveness** (call the provider's token-info
endpoint). Liveness verification takes precision from ~60% to ~99% and is the difference
between a useful feature and a noise generator. Store `sha256(secret)` as the dedupe key,
never the value.

This is the strongest single addition in this document: high severity, genuinely unserved,
trivially reuses existing data, and its finding is unambiguous.

### 1.2 Test impact analysis — as advice, not infrastructure

**Weeks: 3–4 · Confidence: medium · Novelty: high in this form**

Launchable, Gradle Develocity's Predictive Test Selection, and Nx/Turborepo's `affected`
commands all do test selection — and all require adopting their SDK, their build system, or
their agent.

We can compute the *same signal* observationally: for each test and each changed path,
what fraction of the time did touching that path produce a failure in that test? After
months of history, tests that have never once failed for changes in a given directory are
identifiable statistically, with no instrumentation at all.

We then **advise** rather than execute:

> `tests/e2e/billing/` has never failed for a change under `docs/` or `web/static/` across
> 2,140 runs. Gating it on `paths` would have skipped 61% of its executions and never
> missed a failure. **Est. 6.1 min/PR.**

Same value as Launchable, zero adoption cost, and it slots straight into the Phase 2 fixer
pipeline as a `paths:` filter PR. The credibility bar is high — a wrong recommendation
means a missed bug — so this ships with a hard evidence threshold and a `paths` filter
rather than an outright skip.

### 1.3 Preview-environment cost attribution

**Weeks: 2 · Confidence: medium · Novelty: high**

Ephemeral per-PR environments cost real money — roughly $8–25/day per active environment
on AWS/GCP, or $200–600/month for a team opening 20 PRs, plus $200–400/month platform fee
for managed options like Qovery or Bunnyshell.

Nobody attributes that cost per PR, and the dominant waste pattern is well known and
unmeasured: **environments that outlive their PR.** A PR closes, the teardown workflow
fails silently, the environment runs for three weeks.

We already ingest `deployment` and `deployment_status`. Detecting a deployment with no
corresponding teardown after its PR closed is a straightforward join, and the finding is
unambiguous money.

Fits the thesis exactly — waste, quantified, from execution history — and extends us from
CI into CD without building any deployment infrastructure.

### 1.4 Merge-blocking and PR wait decomposition

**Weeks: 2 · Confidence: high · Novelty: medium**

Where does a PR's *calendar* time go — CI, review latency, merge-queue wait, or rework
after a failed check?

We have every check-run timestamp and every PR event. LinearB and Graphite sell PR
analytics, but their unit is the person (throughput, review time per reviewer), which makes
them politically fraught and easy to misuse. Ours is the **pipeline**: "42% of your PR
calendar time is waiting on CI you could halve," which routes straight back into the core
product rather than into someone's performance review.

Deliberately never report per-person metrics. That is a decision about what kind of tool
this is.

---

## Tier 2 — Credible later, not now

### 2.1 Change failure prediction

Score a PR's deployment risk from historical signal: files with high past change-failure
rates, churn, author familiarity with the touched paths, whether affected tests are flaky.
Needs Phase 4's DORA data plus a lot of deployment history to be more than astrology.
Revisit once several installs have 6+ months of deploy data.

### 2.2 Rollback and incident correlation

Detect rollbacks from `deployment_status` sequences and link them to the runs and findings
that preceded them. "This deploy was rolled back; the pre-deploy suite had 3 quarantined
flaky tests" is a strong narrative. Requires deploy volume we won't have early.

### 2.3 Runner fleet sizing (self-hosted)

Recommend instance types and pool sizes from observed queue depth and utilization. Real
money for orgs running self-hosted fleets, but the audience is narrow and it depends on
GitHub's shelved self-hosted pricing, which may return in a different shape.

### 2.4 Cross-repo org rollups

Already the hosted-tier boundary in `PRODUCT.md` §11. Blocked on having orgs, not on
engineering.

### 2.5 Reachability analysis (the deferred Phase 4)

Still the best idea in the original plan and still the right first expansion beyond
pipeline intelligence. Deferred because it needs the sandbox and a call graph. Design in
`PRODUCT_PLAN.md` §8 stands unchanged.

---

## Tier 3 — Do not build (researched and rejected)

Knowing why these are rejected is worth more than the list.

### 3.1 Workflow security scanning

**Rejected: comprehensively served, by better-positioned tools.**

- **zizmor** — static analysis, excessive permissions
- **poutine** — supply-chain, risky pipe patterns
- **actionlint** — script injection, credential misuse
- **[StepSecurity Harden-Runner](https://github.com/step-security/harden-runner)** — an EDR
  for Actions runners: runtime network egress, file integrity, and process monitoring.
  Protects 5,000+ OSS projects and has caught real supply-chain compromises including the
  `tj-actions/changed-files` incident and Sha1-Hulud.

Harden-Runner in particular occupies the *runtime* evidence class we would otherwise
claim, and it does so with an agent inside the runner — strictly more data than our
read-only position can access. There is no gap here.

### 3.2 Merge queue

**Rejected: crowded and commoditized.** GitHub ships one free; Mergify, Aviator, and
Graphite compete hard above it with batching, priority lanes, speculative checks, and
queue freeze. Building a sixth is pure cost. *We should integrate*: a queue is a rich
source of wait-time data for 1.4.

### 3.3 Stacked PRs

**Rejected: Graphite owns the category**, with Aviator's OSS CLI beneath it. It is a
workflow product requiring a CLI, a desktop app, and habit change — orthogonal to
everything we do and sharing no substrate.

### 3.4 Preview environment *provisioning*

**Rejected: infrastructure-heavy, well-served** (Qovery, Northflank, Bunnyshell, Preevy,
Argo CD ApplicationSets with a PR generator). Provisioning means running customer
infrastructure — a different company. Attribution (1.3) is the part that fits us.

### 3.5 Faster runners / runner hosting

**Rejected: active price war.** Depot, Blacksmith, WarpBuild, Namespace, RunsOn, Ubicloud,
BuildJet — and GitHub just cut hosted prices up to 39%, compressing everyone's margin.
Capital-intensive, undifferentiated, and it would make us a vendor whose advice ("use
cheaper runners") is self-interested. Staying neutral is worth more than the revenue.

### 3.6 Code review comments on diffs

**Rejected: the contested surface, by design.** This is `PRODUCT.md` §3 and §4. Entering it
forfeits the structural non-collision with review bots that makes coexistence free.

### 3.7 Build caching / remote execution

**Rejected: requires build-system adoption**, which is the exact cost our positioning
exists to avoid. Bazel+BuildBuddy, Nx Cloud, Turborepo, and Develocity own it. We
*recommend* caching; we don't host it.

---

## Recommended sequencing

If the core lands (Phase 2 ship criteria met, ≥5 installs), add in this order:

| Order | Feature | Weeks | Why this order |
|---|---|---|---|
| 1 | Secret exposure in logs (1.1) | 2 | Highest severity-per-week; unserved; data already held |
| 2 | Preview-env cost attribution (1.3) | 2 | Extends to CD with no new infrastructure |
| 3 | PR wait decomposition (1.4) | 2 | Makes the core finding legible in calendar time |
| 4 | Test impact advice (1.2) | 4 | Highest value, highest credibility bar — needs the most history |

**Six weeks buys the first three.** All four fit inside the slack of a single deferred
phase, and none require a new data source, a sandbox, or a permission we don't already
hold.

That is the shape a feature expansion should have: everything downstream of data we
already ingest, everything expressible as a `Finding` with evidence, and nothing that
requires the user to adopt infrastructure.

---

## Sources

- [rhysd/actionlint](https://github.com/rhysd/actionlint) — workflow linting, verified scope
- [zizmorcore/zizmor](https://github.com/zizmorcore/zizmor) — Actions static analysis
- [step-security/harden-runner](https://github.com/step-security/harden-runner) — runner EDR
- [apache/devlake](https://github.com/apache/devlake) — dev data platform, DORA
- [Kleore](https://www.kleore.com/blog/github-actions-ci-optimization) — CI history analysis
- [CICosts](https://app.cicosts.dev/) — OSS Actions cost tracking
- [Datadog critical path](https://docs.datadoghq.com/continuous_integration/guides/identify_highest_impact_jobs_with_critical_path/)
- [renovatebot/renovate](https://github.com/renovatebot/renovate) — mass-PR patterns
- [openrewrite/rewrite](https://github.com/openrewrite/rewrite) — recipe-based refactoring
- [Sourcegraph Batch Changes](https://sourcegraph.com/batch-changes)
- [arXiv:2602.02307](https://arxiv.org/html/2602.02307v1) — flaky builds in GitHub Actions
- [WithSecureOpenSource/flaky-tests-detection](https://github.com/WithSecureOpenSource/flaky-tests-detection)
- [GitHub Actions 2026 pricing](https://github.com/resources/insights/2026-pricing-changes-for-github-actions)
- [Preview environment platforms](https://northflank.com/blog/preview-environment-platforms)
- [Aviator merge queue](https://www.aviator.co/merge-queue) · [Mergify vs Graphite](https://docs.mergify.com/stacks/compare/graphite/)
