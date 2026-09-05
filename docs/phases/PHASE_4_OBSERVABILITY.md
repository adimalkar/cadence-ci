# Phase 4 — Observability: export first, then surface

**Weeks 21–26 · Two halves, sequenced · Promoted 2026-09-05 from a retention phase to a
product pillar**

---

## What changed, and why it is written down

This phase used to be four weeks of screens whose job was to make an install survive past
month one. It is now a **pillar of the product**: Cadence is an observability platform for
CI as well as an auditor of it. Decision taken 2026-09-05.

The scope doubles, so the reasoning has to survive the person who took it.

**The order is the whole decision. Export first, surface second.** The two are not
alternatives, and shipping them in the wrong order is how this phase fails:

- **Export is leverage.** Our data model is already the shape OpenTelemetry wants — a run
  contains jobs, a job contains steps, each with start and end times. Emitting that as
  traces makes Cadence useful inside Grafana, Datadog or Honeycomb on day one, in a UI the
  user already has open, without asking them to adopt another dashboard.
- **A surface is gravity.** The risk section below has always said dashboard work is
  pleasant, endless, and the least adoption per week of any phase. That is still true. It
  is now second in line rather than cut, and it inherits a hard budget.

Both halves compute the same aggregations. Building the export first means the surface is
rendering numbers that already have an external consumer checking them.

---

## What this phase delivers

**4a — export (weeks 21–22).** CI history as OpenTelemetry traces, metrics in Prometheus
and OTLP form, and findings as alertable events. Cadence becomes the *source*, not the UI.

**4b — surface (weeks 23–26).** The screens that only make sense here: the flaky-cost
report, feedback-loop decomposition, the live run view, and the public calibration
dashboard.

Almost none of this is new computation. It is aggregation, transport, presentation, and one
genuinely novel artifact.

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

**And its corollary, added with 4a:** every number we render, we also export. A figure that
exists only inside our own UI cannot be checked, alerted on, or joined to anything else the
user measures. Being the *source* is a stronger position than being the *dashboard*, and it
is the one nobody in this category occupies — DevLake, LinearB and the CI cost dashboards
are all terminal UIs that data goes into and does not come back out of.

DORA ships because it is four SQL queries over data already in the warehouse and its
absence is an objection in enterprise conversations. It is table stakes, not the product.
It gets one page, not a phase.

---

## 4a — Export (weeks 21–22)

### 4a.1 — CI history as OpenTelemetry traces

A workflow run is a distributed trace and always was. We hold the whole tree already.

| Cadence | OTel | Kind |
|---|---|---|
| `run` | root span | `SERVER` |
| `job` | child span | `INTERNAL` |
| `step` | grandchild span | `INTERNAL` |

Follow the [CI/CD semantic conventions](https://opentelemetry.io/docs/specs/semconv/cicd/cicd-spans/)
— `cicd.pipeline.name`, `cicd.pipeline.run.id`, `cicd.pipeline.task.name`,
`cicd.pipeline.task.run.id`, `cicd.pipeline.result` — plus the VCS resource attributes.

**Pin the semconv version and say so in the exporter.** Those conventions are **Release
Candidate, not stable** (checked 2026-09-05). Attribute names can still move, and an
exporter that silently follows a moving spec breaks a user's dashboards without changing a
line of their config. Record the version we emit as a resource attribute so a mismatch is
diagnosable rather than mysterious.

#### Five things that will go wrong, and what to do about them

These are not hypotheticals — four are already documented failures in this codebase.

**1. Backfilled spans get dropped.** Exporting history means emitting spans timestamped
weeks or months in the past. Most backends refuse them: Datadog, Honeycomb and others
enforce an ingest window measured in hours. A user pointing Cadence at 90 days of history
and seeing an empty dashboard will conclude the feature is broken.

> Make backfill an explicit mode with a stated horizon, warn on the first span older than
> the configured window, and document that live export (via the webhook receiver) is the
> path that always works. Do **not** quietly shift timestamps to make ingest succeed —
> that fabricates history.

**2. Multi-attempt runs produce absurd traces.** GitHub's partial re-run carries the
previous attempt's successful jobs forward *with their original timestamps*. One corpus run
spans 3.76 days for this reason (`CAVEATS` 31). A naive exporter emits a four-day trace.
The fix already exists and must be reused: filter `j.attempt = r.run_attempt`, exactly as
[`audit.py:89`](../../src/cadence/audit.py) does. **One trace describes one execution
attempt.**

**3. Timestamps are second-resolution.** GitHub returns whole seconds. Steps shorter than a
second become zero-duration spans, and a job's children will not tile its span exactly.
State the resolution as a resource attribute and never synthesise sub-second precision we
do not have.

**4. Trace IDs must be deterministic.** Derive the trace ID from `(repo_id, run_id,
run_attempt)` by hash, not randomly, so re-exporting the same run is idempotent rather than
duplicating it. Re-export will happen — after a backfill, after a bug fix, after a schema
change.

**5. Queue time is a gap, not a span.** The interval between a job being created and
starting is runner wait. Represent it explicitly rather than leaving dead air between spans,
because it is the single most decision-relevant number we produce: a queue-bound repo gets
*worse* when parallelised, and only a tool measuring both can say so.

### 4a.2 — Metrics

OTLP and a Prometheus scrape endpoint, from the same aggregations 4b renders.

```
ci_run_duration_seconds{repo, workflow, conclusion}       histogram
ci_queue_wait_seconds{repo, workflow, job, runner_label}  histogram
ci_job_billed_minutes_total{repo, workflow, job}          counter
ci_waste_seconds_total{repo, workflow, rule}              counter
ci_finding_dollars_per_month{repo, rule, basis}           gauge
ci_run_conclusion_total{repo, workflow, conclusion}        counter
```

**Cardinality is the trap.** `branch` and `actor` are unbounded on an active repo and will
melt a Prometheus instance. They belong on spans, where high cardinality is expected and
cheap, and must never appear as metric labels. Make that a lint on the exporter, not a
convention people remember.

`basis` stays a label on the dollars gauge because `PRODUCT.md` §6 forbids blending replay
with projection — and a metric that silently mixes them is exactly the blend, just harder
to notice.

### 4a.3 — Findings as events

Every finding already carries severity, evidence, and a savings estimate. Emit new and
`regressed` findings to a webhook, and as OTel logs/events, so a team can route them into
whatever they already use for alerting.

**Never a gate** (rule 4) applies here too. Cadence emits an event; what a user's alerting
does with it is theirs. We do not page anyone by default.

### Why this half is the differentiated one

The category is full of terminal UIs. DevLake, LinearB, Jellyfish and the CI cost
dashboards all ingest data and render it; none of them hand it back in a form another
system can consume. Meanwhile the observability vendors have CI Visibility products that
want an agent inside your runner.

Cadence's position is the third one and nobody holds it: **read-only, no runner agent, and
it emits standards-compliant telemetry about CI you have already run.** Same substrate
argument as everything in Phase 1 — we have the history, and history is the part the
instrumentation-first tools cannot retrofit.

### Retention is the quiet advantage

GitHub deletes workflow logs after 90 days and offers no cross-run analysis at any horizon.
Our storage is already content-addressed (`log_chunk`, `workflow_blob`), so a year of
history costs little. Once export exists, that history has somewhere to go.

The claim to make is narrow and true: **Cadence is the durable record of CI that GitHub
discards.** The claim *not* to make is that we have data from before we started ingesting —
say `first seen in our archive`, never `first occurred`, the same discipline `PHASE_6`
applies to secret exposure windows.

---

## 4b — Surface (weeks 23–26)

## What actually gets built

### 4b.1 — The flaky-cost report (week 23)

**"Top 10 flaky tests costing you N hours/week."** The single best screen in the product
and the one to lead with in every demo.

Now strictly better than the earlier plan intended, because Phase 3 gave us the build-level
taxonomy: the report covers infrastructure flake too, so it reads *"registry timeouts cost
you 6 hours/week, and here is the retry config that fixes it"* — a category no competing
flaky dashboard can populate.

Both currencies, per `PHASE_1` — hours lead for public repos, dollars for private.

### 4b.2 — Feedback-loop decomposition (week 23–24)

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

### 4b.3 — Trend and regression detection (week 24)

Per-workflow duration and cost over time, with regression alerts on p50/p95 shifts.

**Use a changepoint test, not a fixed threshold.** Fixed thresholds are noise machines —
they fire on every seasonal dip and miss slow drift entirely. A changepoint detector
(PELT, or Bayesian online changepoint) on the duration series finds the commit where the
regression started, which turns "CI got slower" into "CI got slower at `a3f9c21`."

That commit link is the finding's evidence, and it is what makes the alert actionable.

### 4b.4 — Live run view (week 25)

The one screen that only exists because this is now an observability platform, and the only
part of the phase that is not aggregation over history.

[`webhook.py`](../../src/cadence/webhook.py) already receives and queues GitHub deliveries,
so `workflow_run` and `workflow_job` events give near-real-time state. A run in flight,
rendered against **its own history**: which job is running, how long that job usually takes,
and whether this run is tracking ahead or behind its own p50.

> `test (3.12)` · running 6m 40s · p50 for this job is 4m 12s · **p90 is 5m 30s**
> This run is in the slowest decile of the last 200.

That comparison is the differentiator. Every CI UI can show you a spinner. Ours is the only
one that can say the spinner has already gone on longer than it usually does, because the
history is the product.

**Scope guard:** this is a status view, not a log tailer. Streaming live logs means
proxying GitHub's log endpoints in real time, which is a different system with a different
rate-limit profile — and `CAVEATS` 27 says we do not have the credential budget for it.

### 4b.5 — DORA (cut — see below)

Deploy frequency, lead time for changes, change failure rate, MTTR — from
`deployment_status` plus run history. Four queries, one page, no dashboard framework.

Our angle where we have one: **join change failure rate to the flaky data.** A team whose
change-failure-rate looks bad may simply have flaky deploy verification. Nobody else can
separate those two because nobody else has both signals.

### 4b.6 — Public calibration dashboard (week 26)

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

**4a — export**

1. A run exported as a trace **renders correctly in two unrelated backends** — Grafana
   Tempo and one commercial vendor. One backend proves nothing; conventions are only real
   when a second consumer agrees.
2. **Re-exporting the same run produces no duplicates**, verified by test. Deterministic
   trace IDs, or the feature is unusable after any backfill.
3. **No exported trace spans more than one run attempt**, verified against the known
   3.76-day run in the corpus (`CAVEATS` 31). This is a regression test, not a check.
4. Span timestamps and durations reconcile with the run's measured wall-clock within one
   second per span — the resolution GitHub gives us, and no better.
5. **No metric carries an unbounded label.** Enforced by a test over the exporter's label
   sets, not by review.

**4b — surface**

6. A maintainer of a repo we don't own reads the flaky-cost report and **says a number
   surprised them.**
7. Calibration dashboard live, publicly reachable, updating weekly without manual work.
8. Changepoint detection identifies the introducing commit for ≥5 known historical
   regressions in the corpus.
9. Feedback-loop decomposition sums to measured wall-clock within 5%.
10. Live run view reflects a job state change within 60s of the webhook delivery.

Criterion 6 is inherited from the earlier plan and is still the right test. It is the only
one that measures whether the product told someone something true they did not know.

Criteria 2 and 3 exist because both failures are silent. A duplicated trace and a four-day
trace both render — they just render *wrong*, and nobody files a bug against a chart that
merely looks odd. Item 31 in [`CAVEATS.md`](../CAVEATS.md) took three attempts to diagnose
for exactly that reason.

---

## Risks

**Dashboard gravity — now the phase's defining risk, because the budget grew.** Observability
work is pleasant, endless, and produces the least adoption per week of any phase. Six weeks
is a ceiling, not a target.

**The cut order is decided in advance, while nobody is attached to anything:** drop 4b.3
(trends) first, then 4b.4 (live run view). **4a ships regardless** — it is the half with
leverage, and a Cadence that exports clean telemetry and renders three good screens beats
one that renders eight and exports nothing. Never cut 4b.1 or 4b.6.

**Export correctness is invisible when wrong.** A dashboard that is subtly wrong gets
noticed by its user. A trace that is subtly wrong gets ingested, stored, and believed. The
five failure modes in 4a.1 are all of this kind, which is why three of them are ship
criteria rather than review items.

**Semconv churn.** The CI/CD conventions are Release Candidate. Attribute names may still
move, and a user's dashboards break when they do without any change on their side. Pin the
version, emit it as a resource attribute, and treat a semconv bump as a **breaking change
with a release note** — not a silent dependency upgrade.

**Publishing a bad calibration number.** If projection lands at 45% within ±25%, that gets
published. The dashboard is worthless if it only reports good news, and the credibility
gained by publishing an unflattering number exceeds anything gained by hiding it. Decide
this now, before there is a bad number to be tempted by.

**DevLake comparison.** Anyone evaluating us on dashboard breadth will find DevLake wins.
The response is unchanged and got stronger with 4a: we are not an analytics platform. We
find specific defects, prove them from history, fix them, and **emit the evidence in a
standard format**. The dashboards exist to make findings legible over time; the exporter
exists so the numbers do not have to live here at all. Do not get drawn into
feature-matching.

**Becoming a second-rate Datadog.** The failure mode of this phase, stated plainly. Datadog
CI Visibility, Buildkite and CircleCI Insights all have runner-level instrumentation we will
never have. The moment Cadence starts competing on *live telemetry breadth* it loses to
tools with an agent inside the runner. Our ground is the opposite one: **retrospective
evidence over history nobody else kept**, exported into the tools that own the live view.

---

# Execution checklist

Moved from `ROADMAP.md` 2026-08-30. Restructured 2026-09-05 when observability became a
pillar.

## 4a — export (weeks 21–22) · ships regardless of what gets cut

- [ ] **OTLP trace exporter** — run → job → step, following the CI/CD semantic conventions,
      with the semconv version pinned and emitted as a resource attribute
- [ ] **Deterministic trace IDs** from `(repo_id, run_id, run_attempt)` — re-export is
      idempotent, verified by test
- [ ] **`attempt = run_attempt` filter**, reusing [`audit.py`](../../src/cadence/audit.py)'s
      rule. Regression test against the 3.76-day run in the corpus
- [ ] **Queue time represented explicitly**, not left as dead air between spans
- [ ] **Backfill as an explicit mode** with a stated horizon and a warning on the first span
      older than the backend's ingest window. Never shift timestamps to force ingest
- [ ] **Metrics**: OTLP + Prometheus endpoint, from the same aggregations 4b renders
- [ ] **Unbounded-label lint** — `branch` and `actor` on spans only, never metric labels
- [ ] **Findings as events** — new and `regressed` to a webhook and as OTel events. Never a
      gate; we emit, the user's alerting decides

## 4b — surface (weeks 23–26)

- [ ] Flaky-cost report — including infra flake (23)
- [ ] Feedback-loop decomposition: push → queue → execution → post (23–24)
- [ ] Changepoint regression detection, not fixed thresholds (24) — **first to cut**
- [ ] Live run view off the existing webhook receiver, each job against its own p50/p90 (25)
      — **second to cut**. Status view only, not a log tailer
- [ ] **Public calibration dashboard** (26) — predicted vs realized per rule, replay and
      projection reported separately, published whether or not it flatters us
- [ ] ~~DORA — one page, four queries~~ — **cut, see below**

## Ship criteria

**Export**

- [ ] A trace renders correctly in **two unrelated backends**
- [ ] Re-export produces no duplicates, verified by test
- [ ] No trace spans more than one run attempt, verified against the corpus
- [ ] Spans reconcile with wall-clock within 1s — GitHub's resolution, no better
- [ ] No metric carries an unbounded label, enforced by test

**Surface**

- [ ] A maintainer says a number surprised them
- [ ] Calibration dashboard live, updating weekly, unattended
- [ ] Changepoint finds the introducing commit for ≥5 known regressions
- [ ] Feedback decomposition sums to wall-clock within 5%
- [ ] Live run view reflects a job state change within 60s of delivery

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

---

## What 4a unblocks elsewhere

Worth recording, because it is the argument for doing export first rather than last:

- **Phase 6's audit trail.** The event exporter is the same transport an audit stream needs.
  Noted as deferred when Infisical's audit-log streaming was assessed on 2026-09-03; 4a
  makes it nearly free rather than a separate build.
- **Org rollups** (hosted tier, below) stop needing a bespoke API — an org aggregating its
  own repos can scrape the metrics endpoint.
- **The calibration dashboard** (4b.6) becomes a consumer of our own exporter rather than a
  parallel implementation, which means the public numbers and the exported numbers cannot
  drift apart.

The last one matters more than it looks. Publishing our own error rate is the strongest
trust signal we have, and it is worth less if the dashboard computes its figures by a path
nobody else can reproduce.
