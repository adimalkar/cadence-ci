# Phase 4 — Observability and Trust Surface

**Weeks 21–24 · Aggregation over data we already hold · The retention phase**

---

## What this phase delivers

The screens that make an install survive past month one, plus the public calibration
dashboard that makes our claims checkable by strangers.

Almost none of this is new computation. It is aggregation, presentation, and one genuinely
novel artifact.

---

## The honest framing: DORA is commoditized

Generic DORA metrics are a solved, crowded, free problem:

- **[Apache DevLake](https://github.com/apache/devlake)** — Apache-governed, multi-source,
  prebuilt DORA dashboards, extensible via SQL and webhooks. Free.
- **[opendora](https://github.com/DevoteamNL/opendora)**, Google's **Four Keys** — focused
  OSS implementations.
- **LinearB, Sleuth, Faros, Jellyfish** — commercial, with the whole engineering-analytics
  category attached.

**We will not win on DORA.** Shipping a fifth free DORA dashboard is undifferentiated work
that competes with an Apache project.

What we have that none of them do is **step-level timing history joined to workflow
config, plus per-finding realized savings.** Phase 4 must be built on that, not on the
four keys.

### The rule for this phase

> Every screen answers a question the user has money or time riding on, and every number
> traces back to a finding.

DORA ships because it is four SQL queries over data already in the warehouse and its
absence is an objection in enterprise conversations. It is table stakes, not the product.
It gets one page, not a phase.

---

## What actually gets built

### 4.1 — The flaky-cost report (week 21)

**"Top 10 flaky tests costing you N hours/week."** The single best screen in the product
and the one to lead with in every demo.

Now strictly better than the earlier plan intended, because Phase 3 gave us the build-level
taxonomy: the report covers infrastructure flake too, so it reads *"registry timeouts cost
you 6 hours/week, and here is the retry config that fixes it"* — a category no competing
flaky dashboard can populate.

Both currencies, per `PHASE_1` — hours lead for public repos, dollars for private.

### 4.2 — Feedback-loop decomposition (week 21–22)

Where does a PR's wall-clock actually go?

```
Push → first signal      2m 10s   ██
Queue (runner wait)      4m 33s   ████        ← queue-bound
Execution (critical path) 8m 02s  ████████
Post-processing          1m 15s   █
────────────────────────────────
Total p50               16m 00s
```

Decomposing queue vs. execution is the piece that makes advice correct rather than
generic. A queue-bound repo gets *worse* when parallelized, and we are the only tool
positioned to say so, because we measure both.

### 4.3 — Trend and regression detection (week 22)

Per-workflow duration and cost over time, with regression alerts on p50/p95 shifts.

**Use a changepoint test, not a fixed threshold.** Fixed thresholds are noise machines —
they fire on every seasonal dip and miss slow drift entirely. A changepoint detector
(PELT, or Bayesian online changepoint) on the duration series finds the commit where the
regression started, which turns "CI got slower" into "CI got slower at `a3f9c21`."

That commit link is the finding's evidence, and it is what makes the alert actionable.

### 4.4 — DORA (week 23, one page)

Deploy frequency, lead time for changes, change failure rate, MTTR — from
`deployment_status` plus run history. Four queries, one page, no dashboard framework.

Our angle where we have one: **join change failure rate to the flaky data.** A team whose
change-failure-rate looks bad may simply have flaky deploy verification. Nobody else can
separate those two because nobody else has both signals.

### 4.5 — Public calibration dashboard (week 24)

**The most important deliverable in this phase**, and the most important artifact for a
job search.

A continuously-updated public page showing, per rule:

| | |
|---|---|
| Predictions made | n |
| Fixes merged | n |
| **Within ±25% of predicted** | **%** |
| Median predicted vs. realized | min/run |
| Split by basis | replay vs. projection |

Plus methodology, held-out repo list, and the detector version SHA for every number.

Nobody in this category publishes anything comparable. In a market where every claim is an
unverifiable estimate, publishing our own error rate — including where projection performs
worse than replay — is the strongest available trust signal. It is also unfakeable: it can
only exist because Phase 2 closed the loop by recording realized savings from merged PRs.

Expect replay near 95% and projection materially worse. **Publish both separately.**
Blending them to make the headline number look better destroys the artifact's entire value.

---

## Org-level rollups (hosted only)

Cross-repo aggregation for organizations: total CI spend, waste by repo, which teams have
the worst feedback loops, fleet-wide flaky cost.

This is the natural hosted-tier boundary from `PRODUCT.md` §11 — it requires data from
repos a single self-hosted install would not have, so the seam is technical rather than
artificial.

---

## Ship criteria

1. A maintainer of a repo we don't own reads the flaky-cost report and **says a number
   surprised them.**
2. Calibration dashboard live, publicly reachable, updating weekly without manual work.
3. Changepoint detection identifies the introducing commit for ≥5 known historical
   regressions in the corpus.
4. DORA numbers reconcile with GitHub's own Insights where both exist.
5. Feedback-loop decomposition sums to measured wall-clock within 5%.

Criterion 1 is inherited from the earlier plan and is still the right test. It is the only
one that measures whether the product told someone something true they did not know.

---

## Risks

**Dashboard gravity.** Observability work is pleasant, endless, and produces the least
adoption per week of any phase. The 4-week budget is a ceiling, not a target. If it starts
sprawling, cut 4.3 and 4.4 and keep 4.1 and 4.5.

**Publishing a bad calibration number.** If projection lands at 45% within ±25%, that gets
published. The dashboard is worthless if it only reports good news, and the credibility
gained by publishing an unflattering number exceeds anything gained by hiding it. Decide
this now, before there is a bad number to be tempted by.

**DevLake comparison.** Anyone evaluating us on dashboard breadth will find DevLake wins.
The response is that we are not an analytics platform — we are a tool that finds specific
defects and fixes them, and the dashboards exist to make those findings legible over time.
Do not get drawn into feature-matching.

---

# Execution checklist

Moved from `ROADMAP.md` 2026-08-30.

- [ ] Flaky-cost report — including infra flake (21)
- [ ] Feedback-loop decomposition: push → queue → execution → post (21–22)
- [ ] Changepoint regression detection, not fixed thresholds (22)
- [ ] ~~DORA — one page, four queries (23)~~ — **see the cut below**

**F3 — trends (21–22)** · **F4 — public calibration (24)**

- [ ] F3: flaky cost over time, feedback decomposition, regressions with introducing commit
      linked. One screen, not a dashboard suite.
- [ ] F4: **public calibration dashboard** — predicted vs realized per rule, replay and
      projection reported separately, published whether or not it flatters us

## Ship criteria

- [ ] A maintainer says a number surprised them
- [ ] Calibration dashboard live, updating weekly, unattended
- [ ] Changepoint finds the introducing commit for ≥5 known regressions
- [ ] Feedback decomposition sums to wall-clock within 5%

## Two changes to this phase

### Cut DORA

Agreed with the strategy review, for a reason it does not state: DORA metrics make Cadence
legible as *"another engineering analytics platform"*, which is the category we would lose
in. Every hour spent on four commodity queries is an hour not spent on the thing nobody
else can do — evidence from the customer's own execution history.

Cut unless a paying customer asks by name.

### Reframe feedback decomposition as a finding, not a dashboard

The decomposition is genuinely useful, but as a **ranked finding** rather than a chart:

> *"62% of your CI minutes happen before your tests start."*

That ranks against other findings and points at a remedy — cache, prebuilt image, smaller
base container. A stacked bar chart does neither. Detail in
[`../FEATURE_CANDIDATES.md`](../FEATURE_CANDIDATES.md) F7, including the honest constraint:
it needs a step-name classifier, which must refuse to classify what it does not recognise
and report its own coverage, exactly as the critical path withholds below 80% mapping.

**Regression detection and blame moved to Phase 3**, where the attribution machinery
already lives. What stays here is the *trend surface* over it.

## One candidate this phase should absorb

**Required-check long pole** ([`../FEATURE_CANDIDATES.md`](../FEATURE_CANDIDATES.md) F5).
Branch protection decides what blocks a merge; if six required checks finish in four minutes
and the seventh takes fourteen, the seventh **is** the merge wait. One API call plus run
durations already held. It answers the strategy review's *"why is this PR still waiting?"*
for the CI portion without needing review-latency data — which is the half we can measure
honestly.
