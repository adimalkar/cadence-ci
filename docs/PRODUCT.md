# Cadence — What We Are Building

> **This is the canonical product definition.** It supersedes the phase ordering and
> scope in `PRODUCT_PLAN.md`, which is retained as the engineering appendix (schema,
> log normalization, sandbox threat model, detector internals).
>
> Last revised: 2026-08-01

**Document map**

| Doc | Contents |
|---|---|
| **PRODUCT.md** (this file) | What we build, why, in what order |
| [`phases/PHASE_0_INGEST.md`](phases/PHASE_0_INGEST.md) | Ingest platform, weeks 1–3 |
| [`phases/PHASE_1_WASTE_AUDIT.md`](phases/PHASE_1_WASTE_AUDIT.md) | The waste audit, weeks 4–10 |
| [`phases/PHASE_2_FIX_PRS.md`](phases/PHASE_2_FIX_PRS.md) | Remediation PRs, weeks 11–13 |
| [`phases/PHASE_3_FLAKE.md`](phases/PHASE_3_FLAKE.md) | Flaky build intelligence, weeks 14–20 |
| [`phases/PHASE_4_OBSERVABILITY.md`](phases/PHASE_4_OBSERVABILITY.md) | Observability + calibration, weeks 21–24 |
| [`phases/PHASE_5_REVIEW.md`](phases/PHASE_5_REVIEW.md) | Merge readiness + grounded BYO-key review |
| [`phases/PHASE_6_SECURITY.md`](phases/PHASE_6_SECURITY.md) | Security — AI-specific first, then SCA + reachability |
| [`EXPANSION.md`](EXPANSION.md) | Researched feature candidates, ranked, with a do-not-build tier |
| [`PRODUCT_PLAN.md`](PRODUCT_PLAN.md) | Engineering appendix — schema, normalization, sandbox |

---

## Read this part if you read nothing else

**Cadence reads your CI history and tells you what it's costing you — in minutes and
dollars — then opens the pull request that fixes it.**

You point it at a repo. It reads the last few hundred workflow runs through the plain
GitHub API. It comes back with something like this:

> Your PR feedback loop is 22 minutes. The critical path is 8.
> Here is the other 14, ranked by hours recovered:
>
> | # | Finding | Recovered |
> |---|---|---|
> | 1 | `npm ci` runs cold every job — no cache configured | 4.2 min/run · $310/mo |
> | 2 | No `concurrency: cancel-in-progress` — 340 superseded runs finished anyway | $180/mo |
> | 3 | `build` and `lint` serialized via `needs:` but share no artifacts | 3.0 min/run |
> | 4 | `windows-py3.9` matrix leg has never been the sole failure in 1,400 runs | $95/mo |
> | 5 | 4% of tests consume 61% of suite wall time | — |
> | 6 | Integration workflow triggers on `docs/**` — 210 pointless runs | $40/mo |
>
> Apply 1–3: p50 feedback **22 min → 9 min**. **~$490/mo** recovered.
> [Open fix PR for #1] [Show the analysis]

Every number in that report is computed by replaying real historical runs, not estimated
by a model. Every claim links to the runs it came from.

That is the whole product. Everything below is detail.

---

## 1. Why this works

### The problem

CI is slow and expensive, and nobody knows *where* it's slow and expensive. Teams feel
the 22-minute wait every day but can't decompose it. So they either live with it, or they
adopt a big-commitment tool and hope.

### What everyone else sells

| What they sell | Who | Cost to adopt |
|---|---|---|
| Faster runners | Depot, Blacksmith, WarpBuild, Namespace | Route your builds through their machines |
| Build caching / remote execution | Bazel+BuildBuddy, Nx Cloud, Turborepo, Develocity | Migrate your build system |
| Test impact analysis | Launchable, Develocity PTS | Instrument your test runner with their SDK |
| Merge queues | Mergify, Aviator, Graphite, GitHub native | Change how the whole org merges |
| CI observability | Datadog CI Visibility, Trunk, Buildkite | Instrument everything, pay Datadog prices |
| CI cost dashboards | Kleore, CICosts, ActionsCost | Install an App; then do the optimizing yourself |

Every one demands **infrastructure commitment before delivering any value**. You migrate,
instrument, or hand over your builds — and only then find out what you saved.

### The gap

Research into prior art (full detail in [`PHASE_1`](phases/PHASE_1_WASTE_AUDIT.md)) found
the field splits into two camps that never meet:

- **Config readers** — actionlint, zizmor, poutine — parse your workflow YAML, but only for
  correctness, security, and supply chain. Verified directly: *actionlint has no rules for
  caching, duration, cost, or efficiency.* None of them know how long anything took.
- **History readers** — Kleore, CICosts, Datadog — measure where your minutes went, but
  never open your workflow file to say which line caused it.

**Nobody joins config analysis to runtime history.** "Your `needs:` edge on line 34 is
false, and removing it saves 3.0 min/run across your last 1,400 runs" needs both halves.
That sentence is the product.

And more simply: **nobody sells the diagnosis separately from the cure.**

Cadence's read is zero-commitment: no install, no agent, no config change, no build
migration. It uses read-only GitHub API access that you already have. You find out what's
wrong, quantified, before you decide to change anything.

Then remediation is incremental and reversible — a pull request you read and merge, not a
platform you adopt.

### The moat

Not the rule catalog. Anyone can write cache-miss detection in a weekend.

**The moat is the counterfactual simulator.** Because we store step-level timings for every
historical run, we can *replay* a proposed change against real past data instead of
guessing: "with this step cached, these 1,400 runs would have averaged 9.1 minutes, p95
14.3." That is a falsifiable claim backed by rows in a database.

Second-order moat, compounding from week 1: **cross-repo priors on which fixes actually
delivered.** After a few hundred merged fix PRs we know that cache-config fixes deliver a
median 71% of predicted savings and matrix-pruning delivers 94%. Nobody can buy that back
later.

### The nearest competitor

**Kleore** (commercial GitHub App) is the closest thing that ships today: it scans Actions
history, ranks flaky tests, quantifies cost in dollars, and produces a prioritized fix
list. It got there first and it should be taken seriously.

What survives as differentiation, strongest first:

1. **Counterfactual replay** — they report where minutes went; we report what a specific
   change would have done to real past runs.
2. **Config-level rules** — cache keys, `needs:` edges, matrix legs, `concurrency`, path
   triggers. Their surface appears to be tests and compute.
3. **Fix PRs** — nobody in the category does this.
4. **Build-level flake**, not just test-level (§5 class F).
5. **No-install read-only** — we can produce the audit before the user agrees to anything,
   which is the entire cold-pitch strategy.

**Re-verify their feature set before week 8.** If they ship config rules and fix PRs during
our build, differentiator 1 is the only one left and this plan needs revisiting.

---

## 2. The spine (never violate these)

These four rules define the product. Every design argument resolves against them.

1. **Evidence or it doesn't ship.** Every finding cites specific runs, a specific config
   range, or a specific timing series. Enforced by a database `CHECK` constraint and an
   insert trigger, not by code review.

2. **The LLM is never the detector.** Detectors produce findings, savings, and evidence.
   The model receives that as structured input and writes 2–4 sentences of prose. If the
   API call fails, the finding still ships without narrative. Hard-wire the fallback in
   week 4, not later.

3. **Read-only until explicitly invited.** Diagnosis needs no write permission at all.
   Write access is requested only when the user wants fix PRs.

4. **Never a gate.** Check runs conclude `neutral`, never `failure`. Becoming a red X on
   someone's PR is how you get uninstalled.

---

## 3. Scope

### Revised 2026-08-16 — we now review diffs

This section previously read *"Not a code review bot. We never comment on the code in your
diff."* That is reversed, deliberately, and the reversal is recorded rather than quietly
edited because it costs something real.

**What it costs.** §4's coexistence was *structural* — different trigger, different tab,
different file, often a different person — so it was free. Commenting on diffs forfeits
that: we now compete in the PR timeline, and every "too many bot comments" complaint in
this category lands on us too. It also un-defers the KMS envelope-encryption vault, one of
the two hardest items pushed out of Phase 0.

**Why it's still defensible.** Only if the review is *grounded in CI evidence nobody else
has*. A bot that reads a diff and opines is commodity — three funded incumbents already do
it, and it commoditizes again every time a frontier model ships. A bot that says *"you
changed `parse_header`, which appears in the stack trace of 7 flaky failures this month,
and this file caused 11 red builds in 90 days"* cannot be built without months of run
history. That is the only version worth building, and it is the one specified in
[`PHASE_5_REVIEW.md`](phases/PHASE_5_REVIEW.md).

**The rule that keeps it honest:** if the LLM has no Cadence evidence to attach to a hunk,
it stays silent on that hunk. We do not ship generic style commentary.

### Still not building

- **Not a CI runner.** We don't execute your builds. Depot and Blacksmith do that.
- **Not a build system.** We don't ask you to migrate to Bazel or Nx.
- **Not a merge queue.** GitHub ships one free. We *predict* conflicts as findings; we do
  not sequence or gate merges.
- **Not a test framework or a coverage tool.**
- **Not a gate, ever.** Check runs stay `neutral` (§2 rule 4), including review findings.
  Nothing Cadence emits can turn someone's PR red.

---

## 4. Positioning: peer, not plugin

For the pipeline modules (§5–§7) we remain complementary to review bots by construction —
different trigger (`workflow_run.completed` vs `pull_request.opened`), different tab,
different file, often a different person (pipelines are usually owned by whoever set up
CI, not the PR author).

**For the review module (§3, Phase 5) that is no longer true**, and pretending otherwise
would be the expensive kind of self-deception. There we occupy the same surface, at the
same moment, as CodeRabbit and Greptile. Coexistence there has to be *engineered* — see
mechanism 2 below, which stops being hygiene and becomes the feature that decides whether
an install survives.

Coexistence is **hygiene, not growth**. It removes an objection that would have killed an
install; it creates zero pull on its own. Nobody adopts a tool because it's polite to
their other tools.

Four mechanisms, built as infrastructure rather than marketing:

1. **Check Run as primary surface.** Renders markdown, carries native file/line
   annotations, appears in the Checks tab, notifies nobody. The PR conversation timeline
   is the contested surface; the Checks tab is empty.
2. **Deference, now load-bearing.** Pipeline findings: at most one PR comment, for the
   single highest-value finding, edited in place across pushes, never a new comment per
   push. Review findings (Phase 5): **hard-capped at 3 inline comments per PR**, and the
   whole review module defaults **off** when `coderabbitai[bot]`, `greptile-apps[bot]`, or
   `qodo-merge-pro[bot]` is active on the repo. If another bot has already commented
   within ±5 lines of our evidence range, we drop ours. Nobody in this category defers;
   it is ~2 days of work and it is the difference between coexisting and being uninstalled.
3. **SARIF output from day one.** Findings land in GitHub's Security tab for free and any
   other tool can consume them. An afternoon if built into the `Finding` serializer, a
   week if retrofitted.
4. **A read API**, so our data is an input to someone else's tooling.

**We never position as an add-on to a specific vendor.** Being a plugin to CodeRabbit
means your roadmap is hostage to theirs, your ceiling is their install base, you have no
direct user relationship, and the moment your feature matters they build it themselves.
Build so we never fight them; don't pitch as their helper.

---

## 5. The waste catalog

This is the product surface. Each rule is deterministic, evidence-backed, and quantified.

### A — Cache and redundant work
| Rule | Detection | Typical fix |
|---|---|---|
| No dependency cache | No `actions/cache` or setup-action cache; install step duration flat across runs | Add cache config |
| Cache key thrashing | Cache miss rate >40% over trailing 50 runs | Fix key composition |
| Duplicated build | Identical build step in ≥2 jobs, no artifact upload/download between them | Build once, share artifact |
| Cold Docker layers | Full image rebuild every run; no registry or GHA layer cache | Enable layer caching |

### B — Graph and scheduling
| Rule | Detection | Typical fix |
|---|---|---|
| False serialization | Job B `needs:` job A but downloads no artifact A produced | Remove the `needs:` edge |
| Critical path bloat | Wall-clock duration ≫ longest true dependency chain | Reparallelize |
| Queue-bound, not compute-bound | Runner wait time > execution time | More parallelism will make it *worse* — say so |
| **Provisioning tax** | Per-job runner-allocation time × jobs per run | Consolidate jobs; the matrix is over-split |

**Provisioning tax** was found in week-1 data, not designed in advance. A job is not just
its steps — it decomposes as `provisioning → steps → cleanup`, and provisioning is real
billed time that no step edit can remove. Measured: **9.5% of job wall-clock on
`astral-sh/ruff`** (13.6 jobs/run ⇒ ~2.2 min/run of pure overhead, p90 23s) versus **1.7%
on `prettier/prettier`** (3.1 jobs/run ⇒ ~0).

The rule that falls out: *every added parallel job costs ~10s before it executes anything.*
Splitting a suite into 20 shards buys parallelism and pays 200s of provisioning. That is a
real ceiling on the single most universal piece of CI advice, and no competing tool
separates provisioning from execution time — so none of them can locate it.

### C — Trigger and scope waste
| Rule | Detection | Typical fix |
|---|---|---|
| Irrelevant path triggers | Workflow ran on commits touching only paths it can't be affected by | Add `paths-ignore` |
| No run cancellation | Superseded runs completed after a newer push to the same ref | Add `concurrency: cancel-in-progress` |
| Non-discriminating matrix leg | Leg has never been the sole failure across N runs | Move to nightly / merge-only |
| Draft PR full pipeline | Full suite running on draft PRs | Gate on `draft == false` |

### D — Test suite shape
| Rule | Detection | Typical fix |
|---|---|---|
| Long-tail tests | Top 5% of tests by duration consume >50% of suite wall time | Split, parallelize, or move to nightly |
| Never-failing tests | Test has passed N consecutive runs, never failed | Candidate for reduced frequency |
| Retry burn | Wall time consumed by re-runs of the same job | See class F |

### E — Runner fit
| Rule | Detection | Typical fix |
|---|---|---|
| Oversized runner | Job duration insensitive to core count; single-threaded profile | Downsize |
| Undersized runner | OOM kills or swap-thrash timing signature | Upsize (net cheaper) |

### F — Reliability waste
| Rule | Detection | Typical fix |
|---|---|---|
| Flaky retry cost | Same signature failing then passing with no tree change | Quarantine or fix |
| Unchanged-tree failures | Identical tree SHA, different outcome | Near-certain flake |
| **Network / timeout flake** | Connection reset, ETIMEDOUT, DNS, TLS handshake in failure region | Retry with backoff |
| **Registry / dependency flake** | npm/pypi/maven 5xx, checksum mismatch | Mirror, retry, pin |
| **Rate-limit flake** | 429, `API rate limit exceeded` | Token rotation, backoff |
| **OOM** | OOMKilled, exit 137, heap exhaustion | Larger runner |

Class F is where the original plan's flaky classifier lives. It is now **one class in the
catalog**, not the whole product — see [`PHASE_3`](phases/PHASE_3_FLAKE.md).

The four bolded rows come from a 2026 study of 1,960 Java projects on GitHub Actions
([arXiv:2602.02307](https://arxiv.org/html/2602.02307v1)): 3.2% of builds are rerun, 67.7%
of those show flaky behaviour, and **only 65% of flaky builds are caused by flaky tests.**
The rest are network (15.8%), dependency resolution (6.32%), environment (4.7%), rate
limits, concurrency, and OOM.

Every competing flaky product — Kleore, BuildPulse, Datadog, Trunk — is scoped to the 65%,
because their unit of analysis is the test. **Ours is the build.** That third of the
problem is unserved, easier to detect than test flake, and usually has a *config* fix,
which means it feeds the Phase 2 fixer pipeline.

### Two currencies: hours and dollars

GitHub cut hosted-runner prices by up to 39% on 2026-01-01 (Linux 2-core $0.008 → **$0.006**
/min, Windows $0.016 → **$0.010**, macOS $0.080 → **$0.062**, each now including a $0.002/min
platform charge). A separate self-hosted charge was announced for March 2026 and then
shelved indefinitely after backlash.

Two consequences, both load-bearing:

**Rate cards are a versioned table, never constants.** They moved once this year and the
shelved charge may return. Every finding stores its `rate_card_version` so historical
savings claims stay auditable.

**Public repos get standard hosted runners free** — which breaks the dollar pitch for
exactly the OSS corpus we cold-pitch in §10. The fix is to denominate waste in whichever
currency the repo actually pays:

| Repo type | Headline | Dollars? |
|---|---|---|
| Public, standard runners | **Wall-clock: contributor wait, PR feedback latency** | Only as "would cost $X if private" |
| Public, larger runners | Both | Yes — larger runners are billed on public repos |
| Private / org | **Dollars** | Yes |

Compute both always; choose the headline by repo type. Quoting an invoice to a maintainer
whose CI is free is the fastest way to lose the pitch.

### Every finding carries

- **Evidence** — the run IDs, the config file range, the timing series.
- **Quantified saving** — minutes per run, and dollars per month where dollars are real.
- **Confidence** — near-1.0 for deterministic config rules; calibrated probability for
  statistical ones.
- **A fix** — a concrete diff, or an explicit "no safe automatic fix; here's the manual
  change."

---

## 6. The counterfactual simulator

The differentiator. Two modes, and the distinction must be visible in the UI because one
is far stronger than the other.

**Replay (strong).** For changes whose effect is subtractive — removing a `needs:` edge,
cancelling superseded runs, pruning a matrix leg, skipping on paths — recompute historical
runs with the change applied using stored step timings. This is arithmetic over real data,
not prediction. Report the full distribution: p50, p95, worst case, n.

**Projection (weaker, label it).** For changes that introduce new behavior — adding a cache
where none existed — we don't have observed data for the new state. Estimate from
comparable steps in the same repo where possible, then from the cross-repo corpus. Always
render as a range with its basis stated: "estimated 3.1–4.8 min/run, based on 340
comparable Node projects."

**Never blend the two into one number.** The credibility of the whole product rests on the
strong mode not being contaminated by the weak one.

**Savings calibration is our headline metric** — see §9.

---

## 7. Roadmap

Assumes ~15 hrs/week.

| Phase | Weeks | Cum. | Deliverable |
|---|---|---|---|
| 0 — Ingest platform | 1–3 | 3 | Read-only ingest, schema, log store, job queue |
| 1 — Waste audit | 4–10 | 10 | Catalog A–E, critical path, simulator, check run |
| 2 — Fix PRs | 11–13 | 13 | Auto-generated remediation PRs with simulated savings |
| **← résumé line** | | **13** | **Portfolio-complete, demoable, has users** |
| 3 — Flaky intelligence | 14–20 | 20 | Class F: clustering, classifier, blame candidates |
| 4 — Observability | 21–24 | 24 | DORA, trends, org rollups, public precision dashboard |

**24 weeks, résumé line at 13.** Two structural improvements over the earlier ordering:

- **Phase 0 shrank from 5 weeks to 3** because the waste audit executes no untrusted code.
  The sandbox and the BYO-key vault — the two hardest and slowest items — are not needed
  until much later, and are deferred out of the critical path entirely.
- **The flaky classifier's data problem solves itself.** The earlier plan's "unavoidable
  collection tail" was real: the ML needed months of history that didn't exist yet. By
  running ingest from week 1 and building the classifier at week 14, four months of run
  history is already sitting in the database when the model needs it.

### Deferred out of the core line

Static analysis orchestration (Semgrep/ESLint/ruff/gosec), SCA/SBOM, secrets, and
reachability analysis — the earlier plan's Phases 3–4 — are **not part of the core
product**. Reasons: they need the sandbox, they collide directly with review bots, and
source-code analysis dilutes a product identity that is now cleanly *pipeline efficiency
and reliability*.

Reachability remains the single best idea in that set and is the obvious first expansion
if the core lands. The design in `PRODUCT_PLAN.md` §8 stands; it's a sequencing decision,
not a rejection.

### Slip discipline

If any phase exceeds 150% of budget, cut to its deterministic core and move on. Do not
extend.

---

## 8. Data model changes

The `Finding` / `Evidence` schema in `PRODUCT_PLAN.md` §4 stands, with three additions.

**Savings columns on `finding`:**

```sql
ALTER TABLE finding ADD COLUMN est_seconds_saved_per_run  real;
ALTER TABLE finding ADD COLUMN est_dollars_per_month      real;
ALTER TABLE finding ADD COLUMN savings_basis              text;
       -- 'replay' | 'projection_intra_repo' | 'projection_corpus'
ALTER TABLE finding ADD COLUMN realized_dollars_per_month real;
       -- backfilled after a fix PR merges; this is the calibration ground truth
```

**New evidence kinds:** `timing_series` (per-step durations across a run window),
`counterfactual` (the simulation input, output distribution, and basis).

**Dedupe key for waste findings:** `hash(rule_id, workflow_path, job_name)`. Deliberately
excludes line numbers so a workflow-file edit doesn't orphan a user's suppression.

---

## 9. Measuring whether it works

The deterministic rules are ~100% precise by construction — a cache is either configured
or it isn't. So precision is the wrong headline metric here. The real question is whether
the *savings numbers* are true.

**Savings calibration** — of merged fix PRs, the share whose realized saving lands within
±25% of predicted, measured by comparing run durations for 30 days before and after the
merge commit.

Nobody in this category publishes anything like it. It's the strongest available trust
signal in a market where every claim is an estimate, and it doubles as the best possible
interview artifact.

- **Target: ≥70% within ±25% by week 13**, separated by basis (replay vs projection —
  replay should be near 95%; projection is where the error lives).
- **Held-out repo set:** choose 10–15 at week 1, write them into `docs/HELDOUT.md`, never
  look at their data during development. Repo-level splits, not sample-level.
- **In-product feedback button** on every finding, writing a labeled row to the eval set.
- **Publish weekly** — calibration, precision, n, date, detector version SHA.

Write the eval harness in Phase 1 and reuse it for every later module. Once is a week;
retrofitting it four times is a month.

---

## 10. Distribution

Starts week 1, not week 13. This is the hardest problem in the plan.

- **Weeks 1–3.** No-install ingest against ~50 public repos with visibly painful CI —
  heavy integration suites, browser tests, anything already using a `flaky` label. Zero
  permission required.
- **Week 8.** Start pitching. The deterministic rules need no training data, so audits are
  generatable as soon as the catalog lands — five weeks earlier than the old plan's flaky-
  led pitch allowed.
- **The cold open.** Generate the report *before* asking for anything: "I analyzed your
  last 500 CI runs. Your pipeline wastes ~$490/month and 13 minutes per PR. Full data
  attached, here are three PRs that fix it. No install needed; happy to keep it that way."
  Lead with the finished analysis, never the install link.
- **Conversion ~20–30%.** Pitch 60 repos to land 15 installs. Plan for the funnel, not the
  target.
- **Keep every install running through week 24.** Six months of uptime while shipping
  migrations is the actual story; the module list is just what you did.

The audit works on repos with *clean* CI too — it just returns a smaller number — which
roughly doubles the addressable target list versus a flaky-only pitch.

---

## 11. Open-core seam

Decide now, not in week 20. Apache-2.0, not AGPL — AGPL scares off exactly the corporate
users who'd become reference installs, and the moat is data, not code.

| Open source (Apache-2.0) | Hosted only |
|---|---|
| All detectors, rules, simulator | Cross-repo fix-effectiveness priors |
| Full schema, migrations, API | Public calibration dashboard |
| Web UI, check-run reporter, SARIF | Managed ingest at scale |
| Self-host via docker-compose | Org-level rollups |

Ship a **prior-sharing opt-in** for self-hosted installs: send anonymized rule outcomes
(no repo names, no log text, no code) and receive the global fix-effectiveness priors in
return. Makes the network effect real instead of theoretical.

---

## 12. Cost model

| Item | Monthly |
|---|---|
| VPS (4 vCPU / 8GB, workers + Postgres) | $25–40 |
| Object storage (R2, ~200GB compressed logs) | $5 |
| LLM (narrative only, ~4k tok/finding, Haiku-class) | $10–25 |
| Domain + misc | $5 |
| **Total** | **~$45–75** |

Keeping the model out of the detection path is what keeps this at $50 instead of $500 —
the concrete payoff of rule #2 in §2, and worth saying out loud in an interview. Cap
per-installation spend; degrade to no-narrative mode when exceeded.

---

## 13. Kill criteria

Written now, while unattached to any of it.

- **Median audit across 50 public repos finds <10% recoverable wall time at week 10** →
  the premise is wrong. Stop and reconsider, don't build Phase 2.
- **Savings calibration <60% within ±25% at week 13** → drop the dollar figures, ship raw
  diagnostics only. Still a useful product, weaker pitch.
- **<5 installs by week 18** → the problem is distribution. Stop building modules; a sixth
  module does not fix distribution.
- **Flaky precision <75% on held-out repos at week 20** → ship the deterministic core
  (unchanged-tree failures, retry cost) and drop the classifier.
- **Any credential leak** → stop all feature work until resolved and disclosed.

---

## 14. Week 1 checklist

1. `git init`; Apache-2.0 LICENSE; README stating §1's thesis.
2. Postgres schema from `PRODUCT_PLAN.md` §4 plus §8 above, with the evidence `CHECK` and
   the insert trigger.
3. Read-only ingest against 50 public repos — behind a `CIProvider` interface
   (`fetch_runs`, `fetch_logs`, `normalize_event`, `post_result`), GitHub Actions as the
   only implementation. History starts accumulating today; it's the one input that can't
   be bought back later.
4. Pick the 10–15 held-out repos, write them into `docs/HELDOUT.md`, never look again.
5. Build the first three catalog rules end to end — no cache, no cancellation, false
   serialization — to prove the finding→evidence→savings→check-run path before widening
   the catalog.
