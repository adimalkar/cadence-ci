# Phase 1 — CI Waste Audit

**Weeks 4–10 · The product · First user-facing surface**

---

## What this phase delivers

Point Cadence at a repo. It returns a ranked, quantified list of what its CI wastes and
what each fix is worth, with every number traceable to real runs.

This is the phase that has to work. Everything before it is plumbing; everything after it
is extension.

---

## The white space (this is the important part)

Research turned up a crowded neighbourhood, but the crowd splits cleanly into two camps
that do not talk to each other:

**Camp A — tools that read your workflow config, but have no runtime history.**

- [actionlint](https://github.com/rhysd/actionlint) — syntax, expression type-checking,
  action inputs, shellcheck integration, `needs:` validation. Verified against the repo:
  **it has no rules for caching, duration, cost, or efficiency.** Explicitly scoped to
  correctness.
- [zizmor](https://github.com/zizmorcore/zizmor) — security static analysis; finds
  excessive permissions.
- **poutine** — supply-chain risk; finds risky `curl | sh` patterns.

All three read the same YAML we do. None of them know how long anything took.

**Camp B — tools that read your runtime history, but never look at your config.**

- **Kleore** (commercial GitHub App) — scans Actions history, ranks flaky tests, quantifies
  cost in dollars, produces a "prioritized fix list." **The closest competitor found.**
- **CICosts** (open source GitHub App) — cost tracking, dashboards, budget alerts. Tracking
  only; the optimizing is left to the user.
- **ActionsCost** — commercial cost dashboard.
- **Datadog CI Visibility** — does do critical-path analysis, and does it well. Requires
  instrumentation and a Datadog contract.

None of them open your workflow file and tell you which line is the problem.

### The gap

**Nobody joins config analysis to runtime history.** "Your `needs:` edge on line 34 is
false — job B downloads no artifact from job A — and removing it saves 3.0 min/run across
your last 1,400 runs" requires both halves. Camp A can say the first clause. Camp B can
say the second. Only a tool holding both can say the sentence.

That, plus the counterfactual simulator and the fix PR, is the defensible position.

### Honest assessment of Kleore

It is genuinely close and it shipped first. Our surviving differentiators, ranked by
strength:

1. **Counterfactual replay.** They report where minutes went; we report what a specific
   change would have done to real past runs. Diagnosis vs. prescription.
2. **Config-level rules.** Their surface appears to be tests and compute. Cache keys,
   `needs:` edges, matrix legs, `concurrency`, and path triggers are config defects and
   look unserved.
3. **Fix PRs** (Phase 2). Nobody does this.
4. **No-install read-only.** They require an App install; we can produce the audit before
   the user has agreed to anything, which is the entire cold-pitch strategy.
5. **Open source.** Table stakes for OSS adoption, not a reason anyone picks us.

**Re-verify Kleore's feature set before week 8.** If they ship config rules and fix PRs
during our build, differentiator 1 is the only one left and the plan needs revisiting.

---

## Pricing reality (changed January 2026 — affects every number we print)

| Runner | Rate/min (all-in) | Was |
|---|---|---|
| Linux 2-core | **$0.006** | $0.008 |
| Windows 2-core | **$0.010** | $0.016 |
| macOS | **$0.062** | $0.080 |

GitHub cut hosted-runner prices by up to 39% on 2026-01-01 and folded a $0.002/min
platform charge into the listed rates. A separate self-hosted charge was announced for
March 2026 and then **shelved indefinitely** after backlash.

Two consequences:

**Rates must be a versioned config table, not constants.** They changed once this year and
a shelved charge may return. Store `rate_card_version` on every finding so historical
savings claims stay auditable.

**Public repos get standard hosted runners for free.** This breaks the dollar pitch for
exactly the corpus we planned to cold-pitch. See "Two currencies" below — this is a real
problem with a real fix, but it must be designed in, not patched later.

---

## Two currencies: hours and dollars

Waste is denominated in whichever currency the repo actually pays.

| Repo type | Lead with | Dollars? |
|---|---|---|
| Public, standard runners | **Wall-clock: PR feedback latency, contributor wait, maintainer time** | Only as "would cost $X if private" |
| Public, larger runners | Both | Yes — larger runners are billed on public repos |
| Private / org | **Dollars** | Yes |

For the OSS corpus the honest headline is *"your contributors wait 22 minutes for a
signal"* — which is a real and widely-felt pain — not a fabricated invoice. Overclaiming a
dollar figure to a maintainer who knows their CI is free is the fastest way to lose the
pitch. Compute both; choose the headline by repo type.

---

## The rule catalog

Full table in `PRODUCT.md` §5. Implementation notes on the ones that carry the weight:

### Cache rules

Detection: workflow declares no `actions/cache` and no `cache:` on a setup action, **and**
the install step's duration is flat across runs (a working cache produces bimodal
durations — fast on hit, slow on miss). Flat + slow = no cache. Bimodal with a high slow
fraction = cache key thrashing.

Cache hit rate is strongly determined by key composition, and published rates give us
usable projection priors:

| Key strategy | Typical hit rate |
|---|---|
| Run ID only | ~0% |
| Branch only | ~30% |
| Branch + OS | ~45% |
| Branch + `hashFiles()` | ~75% |
| OS + `hashFiles()` | ~90% |
| `setup-*` built-in cache | ~95% |

A key containing `github.run_id` is a deterministic 0%-hit bug and is worth a
high-confidence finding on its own — the cache is written every run and never read.

### False serialization

Job B declares `needs: A`. Parse both jobs: if B has no `actions/download-artifact` for
anything A uploads, no dependency on A's `outputs`, and no reference to A's job context,
the edge is likely ordering-only. Confidence drops if either job uses a shared external
resource (a service container, a deployment target).

Savings computed by **replay**: recompute historical run durations with the edge removed
and the DAG re-levelled. Pure arithmetic over stored step timings — the strongest evidence
class we produce.

### Non-discriminating matrix legs

For each matrix leg, count runs where that leg failed **and no other leg failed**. A leg
that has never been the sole failure across a large N has never caught a bug alone. Report
with n and the observation window; never recommend removal below ~200 runs.

Prior art note: matrix explosion is the single most-cited waste pattern in every cost
writeup found, and one report attributes a $4,200/month bill largely to a matrix that grew
to six Node versions unnoticed. This rule alone justifies the phase.

### Missing run cancellation

Detection: two runs on the same ref where the older one completed *after* the newer one
started. Sum the wasted duration. Fix is a three-line `concurrency:` block. Replay is
exact — we know precisely which runs would have been cancelled and when.

### Critical path

Build the job DAG from `needs:`, level it, and compute the longest path using real step
timings. Report wall-clock vs. critical path vs. theoretical minimum (longest single job).

Datadog does this already, so it is not novel — but it is the frame that makes every other
finding legible, and it is what turns a list of issues into "22 minutes, here's the 14 you
can get back."

Also compute **queue time separately.** If runner wait exceeds execution time, the repo is
queue-bound and *more parallelism makes it worse*. Saying so is a differentiator: every
other tool's advice is "parallelize more."

---

## The simulator

Detailed contract in `PRODUCT.md` §6. The engineering rule:

**Replay and projection never mix into one number.** Replay is arithmetic over observed
step timings — removing an edge, cancelling a superseded run, dropping a matrix leg,
skipping on a path filter. Projection estimates unobserved states — mainly "what if a
cache existed here," where we have no observation of the cached state and must reason from
comparable steps.

Every finding stores `savings_basis` ∈ `replay | projection_intra_repo | projection_corpus`
and the UI renders them differently. Replay gets a point estimate with a distribution.
Projection gets a range with its basis stated inline: *"estimated 3.1–4.8 min/run, based
on 340 comparable Node projects."*

The credibility of the entire product rests on replay not being contaminated by projection.

---

## Output surface

Check Run named `cadence/audit`, conclusion **always `neutral`**. Evidence spans become
check-run annotations on the exact workflow YAML lines. SARIF emitted alongside.

At most one PR comment, highest-value finding only, edited in place across pushes, default
**off** when another review bot is detected on the repo.

For no-install repos there is no check run — output is a standalone report page plus a
markdown blob suitable for pasting into an issue. That report *is* the cold-pitch artifact
from `PRODUCT.md` §10.

---

## Build order

Weeks 4–5 are deliberately narrow: prove the whole path end to end on three rules before
widening the catalog. A rule is cheap; the finding→evidence→savings→surface pipeline is
not.

| Weeks | Work |
|---|---|
| 4–5 | Three rules end to end: no-cache, no-cancellation, false serialization. Finding + evidence + savings + check run. |
| 6 | Critical path + DAG levelling. Replay simulator. |
| 7 | Catalog classes A–C complete. |
| 8 | Cost model, rate card table, two-currency reporting. **Start cold pitches.** |
| 9 | Classes D–E. Projection simulator with corpus priors. |
| 10 | Eval harness, calibration measurement, report page. |

Pitching starts week 8 — five weeks earlier than the old flaky-led plan allowed, because
deterministic rules need no training data.

---

## Ship criteria

1. Audit runs against all 50 corpus repos without manual intervention.
2. Median repo surfaces **≥3 findings with ≥10% combined recoverable wall time.**
3. Replay savings reconstruct known-good historical durations within 2%.
4. Zero findings without evidence rows (enforced by the DB, verified by a test).
5. Three maintainers of repos we don't own confirm a finding surprised them.

Criterion 5 is the real one. The others are necessary and insufficient.

---

## Risks

**The premise could be wrong.** If well-maintained repos have <10% recoverable waste, the
product has no market. This is why criterion 2 exists at week 10 and why the matching kill
criterion fires before Phase 2 starts. Test it early and honestly.

**Rules are cheap to clone.** Any competent engineer can write cache-miss detection in a
weekend. The defence is the simulator, the fix PRs, and eventually the corpus priors — not
the rule list. Do not over-invest in catalog breadth at the expense of the simulator.

**False positives on `needs:` edges.** Recommending removal of a real dependency breaks
someone's build. Require positive evidence of independence, not merely absence of evidence
of dependence, and hold this rule to a higher confidence bar than the rest.
