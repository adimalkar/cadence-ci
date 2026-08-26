# Phase 2 / 3 — candidate problems from the field

Researched 2026-08-26. Candidates for the Phase 2 fixer catalog and the Phase 3 failure
taxonomy, drawn from primary GitHub sources, developer threads, and scans of our own
corpus. Design lives in [`PRODUCT.md`](../PRODUCT.md); the checklist is
[`ROADMAP.md`](../ROADMAP.md). This file is neither — it is a shortlist with evidence
attached, so the decision to build or drop each item can be made on numbers.

Nothing here is committed work. Ranking is at the bottom.

---

## Method

- **Hacker News** — 1,546 comments across the three highest-signal threads, pulled via
  the Algolia API and classified by topic:
  [Pricing Changes for GitHub Actions](https://news.ycombinator.com/item?id=46291156)
  (802 pts, 819 comments, 2025-12-16),
  [The Pain That Is GitHub Actions](https://news.ycombinator.com/item?id=43419701)
  (704 pts, 562 comments), and
  [I hate GitHub Actions with passion](https://news.ycombinator.com/item?id=46614558)
  (490 pts, 341 comments).
- **Primary GitHub sources** — pricing page, Actions limits and caching docs, changelogs,
  `actions/runner-images` deprecation issues, community discussions.
- **Reddit** — [r/devops, "What's your biggest frustration with GitHub Actions (or CI/CD in
  general)?"](https://www.reddit.com/r/devops/comments/1rdrpzz/whats_your_biggest_frustration_with_github/)
  (2026-02-24, score 55, upvote ratio 0.77, 96 usable comments). Reddit blocks direct
  access from this environment; the thread was retrieved through the
  [Arctic Shift](https://arctic-shift.photon-reddit.com) public archive, with Reddit's own
  `.rss` endpoint as a second route. Recording the method because it will be needed again.
- **Our own corpus** — runner labels and `timeout-minutes` coverage sampled directly from
  the workflow files of corpus repos.

### The two audiences do not agree, and the disagreement is the finding

| Topic | HN (1,546) | Topic | Reddit (96) |
|---|---|---|---|
| runner performance / queue | 309 | **YAML / syntax / debugging** | **22** |
| billing / minutes / cost | 288 | secrets / environments | 18 |
| debugging + logs | 229 | runner / self-hosted | 10 |
| timeouts / hangs | 143 | **local repro / feedback loop** | **10** |
| self-hosted + third-party | 129 | cost / minutes | 9 |
| YAML / expressions | 115 | flaky / rerun | 6 |
| caching | 74 | monorepo / triggers | 5 |
| flakiness / retries | 15 | caching | 4 |
| matrix | 11 | reliability / outage | 4 |

HN ranks **cost** first; r/devops ranks **debuggability and the edit-test loop** first and
puts cost fifth. Same platform, same year, opposite orderings. The most likely explanation
is audience: HN threads were framed around a pricing announcement and skew toward people
who own a bill, while r/devops skews toward people who own a pipeline day to day.

For Cadence this matters directly, because **the audit report is priced in the HN currency
and read by the Reddit audience.** One r/devops comment states the resulting go-to-market
problem better than our own docs do:

> CI/CD pain is mostly ignored until it becomes too expensive — slow pipelines, flaky
> tests, wasted compute — everyone kind of knows it's there, but nobody really touches it.
> Then one day the bill or wait time gets bad enough and suddenly it's urgent.

That is the cold-pitch thesis from §10, written by a stranger. It also implies the trigger
is *the bill*, not the report — so the report's job is to arrive before the bill does.

---

## C0 — The rate card is stale, and it is stale in the direction that understates us

**This is the highest-priority item in this document, and it is not a Phase 2 or Phase 3
feature. It is a correctness bug in shipped Phase 1 output.**

[`cost.py`](../../src/cadence/cost.py) opens with this reasoning:

> Rates are versioned rows in `rate_card`, never constants: GitHub cut hosted prices up to
> 39% on 2026-01-01, **and a shelved self-hosted charge may yet return.**

The self-hosted charge is not shelved. It took effect **2026-03-01** — nearly six months
before this was written. From
[GitHub's own pricing page](https://github.com/resources/insights/2026-pricing-changes-for-github-actions):

- A **$0.002 per-minute Actions cloud platform charge** applies to all workflow
  executions, GitHub-hosted *and* self-hosted.
- For self-hosted runners the charge begins **March 1, 2026**.
- Hosted runner prices fell **up to 39%** on **January 1, 2026**, with the platform charge
  already folded into the reduced meter price.
- **Public repositories remain free**, on hosted and self-hosted alike.
- On private repos from March 1, 2026, self-hosted runner minutes **consume the free usage
  quota** the same way standard runners do.

### What our code does today

`RateCard.usd_per_minute` returns `0.0` for any label not in the table, deliberately:

> Unknown labels (self-hosted, custom pools) bill at 0.0 rather than guessing -- a made-up
> rate would silently fabricate the dollar column.

That was correct when self-hosted minutes were free. Since 2026-03-01 it under-bills every
self-hosted job on a private repo by $0.002/min. The `rate_card` table carries ten rows,
all GitHub-hosted (`ubuntu-*`, `macos-*`, `windows-*`), version 2026, effective 2026-01-01.
There is no row for self-hosted or third-party runners.

This is not hypothetical for our corpus. Sampling runner labels across corpus repos returns
`depot-ubuntu-24.04-4`, `depot-ubuntu-24.04-8`, `depot-ubuntu-24.04-arm-8`,
`depot-ubuntu-22.04-arm-4`, `depot-ubuntu-22.04-16`, `ubuntu-latest-8core`, `ubuntu-slim`
and `codspeed-macro` — none of which are in the rate card.

### A second, separate defect in the same file

`usd_per_minute` and `hypothetical_dollars_per_month` disagree about unknown labels:

- `usd_per_minute` → `0.0` (the documented "never fabricate" rule)
- `hypothetical_dollars_per_month` → `rates.get(label, 0.006)` — the **hosted Linux** rate

So one self-hosted job priced through the real-bill path is $0.00/min, and through the
"what if this repo were private" path is $0.006/min — 3× the actual $0.002 charge. Both
numbers can appear in the same report.

### Why this matters commercially, not just numerically

The teams most likely to buy a minute-reduction tool are the ones already paying attention
to CI cost, and that population migrated to self-hosted and third-party runners
specifically to escape per-minute billing. 129 of 1,546 HN comments discuss self-hosted or
third-party runners. Those teams now pay per minute again, and one commenter states our
thesis for us:

> The lever that matters the most with the new $0.002/min tax is to reduce the number of
> minutes consumed.

Today Cadence quotes that audience **$0/month in savings**.

**Work:** add self-hosted / unknown-label rows at $0.002 with a `free_on_public` flag;
reconcile the two fallbacks; bump `rate_card_version`; re-run `evalsweep`. Because every
finding stamps its `rate_card_version`, historical claims stay auditable — the versioning
design already anticipated exactly this. **This is the design paying off, and it is worth
doing before any Phase 2 fixer ships a dollar figure.**

---

## Phase 2 candidates — config → evidence → fix PR

### P2-A · Missing `timeout-minutes` → the six-hour default burn

**Strongest new fixer candidate.**

GitHub's default job timeout is 6 hours. A hung job bills silently until it is killed. A
documented case had a command that normally ran under 1.5 minutes hang and consume
**240× its expected minutes, exhausting a monthly budget in 24 hours**.

Measured in our corpus (8-repo sample of workflow files): **~145 job blocks, 31
`timeout-minutes` declarations** — roughly four jobs in five are unprotected. Timeouts and
hangs are 143 of the 1,546 HN comments.

Why it belongs to us rather than to a linter: the fix is a one-line YAML insertion, which
is the exact shape of the `cache.*` and `concurrency.cancel` fixers — but the *value* is in
the number chosen. A linter says "add a timeout." We hold p50/p99 step timings per job and
can say **"your p99 is 4m12s across 87 runs; set 15."** That is the difference between
advice and evidence.

**Open design question, and it is not small.** The saving is *contingent* — it materialises
only when a hang actually occurs. The credibility rule in `PRODUCT.md` §6 admits exactly two
render classes: replay (solid, point value) and projection (hatched, range). Risk avoided is
neither. This needs either a third class or a rule that the finding may only quote dollars
when historical hangs exist in the ingested window. **Decide this before building the
detector, not after.**

- Detector: `no_job_timeout` · Fixer: `timeout.add`
- Evidence: job config + p99 duration distribution + any historical run at/near 360 min

### P2-B · Cache evicted before it is ever reused

Sibling of the shipped `cache_key_never_hits`: the key is *correct*, the entry was simply
evicted before anything could reuse it. Different evidence, different fix.

GitHub's own docs name this failure mode as **cache thrashing**, "where caches are created
and deleted at a high frequency." The relevant mechanics:

- 10 GB per repository, 7-day retention, LRU eviction.
- The eviction sweep moved **from every 24 hours to hourly**, and GitHub's changelog
  concedes this "could lead to additional cache thrashing."
- Since 2025-11-20 the limit can be raised past 10 GB — for money.
- A [community discussion](https://github.com/orgs/community/discussions/156773) reports
  eviction is not actually least-recently-*used* but least-recently-*created*, which breaks
  the mental model people optimise against.

Caching is 74 of 1,546 HN comments, and the qualitative complaints centre on cold-start
cost — the migration testimonials ("Rust builds went from 20+ minutes to 4-8 minutes") are
consistently about cache locality, not raw CPU.

- Detector: `cache_evicted_before_reuse`
- Evidence: save/restore log pairs, hit-rate decay over the window, total cache footprint
  vs the 10 GB ceiling
- Fix: narrow cache scope, or de-duplicate near-identical caches written by every matrix leg

### P2-C · Action and runner-image version rot

Real, dated, and **the weakest candidate here.** Recorded so the reasoning is not
re-litigated later.

`ubuntu-22.04` deprecates **2026-09-17** with build-failing brownouts and retires
**2027-04-17** ([`actions/runner-images#14254`](https://github.com/actions/runner-images/issues/14254),
opened 2026-06-16). Separately, actions pinned to Node 20 runtimes are deprecated — this
repo's own CI hit it on `checkout@v4`, `setup-uv@v5` and `upload-artifact@v4`.

**Our corpus cannot demonstrate the problem.** A scan of 55 corpus repos found **zero**
exposed to the `ubuntu-22.04` brownout. Sampled runner labels are `ubuntu-latest` ×56 and
`ubuntu-24.04` ×2; the only `22.04` strings are `depot-ubuntu-22.04-*`, which are Depot's
third-party runners and unaffected by GitHub's brownout. Well-maintained OSS stays current.

Two independent reasons to rank this last: Dependabot and Renovate already occupy the
ground, and the HN threads show the pinning question is *contested* rather than settled —

> Pinning dependencies is trading one problem for another.

Anything we build here argues with an existing tool on its own turf, using a corpus that
shows no instances.

### P2-D · Pipeline-fix-loop churn — the "fix: just make it work" runs

**New candidate, straight out of the r/devops thread, and the clearest measurable waste in
it.** There is no local way to test a workflow, so the edit-test loop *is* the CI pipeline.
Every attempt at fixing a workflow is a commit, and every commit is a billed run.

The thread says it plainly. Second-highest-scoring reply in the whole discussion:

> "fix: yaml syntax error" · "fix: typo" · "fix: just make it work" · "fix: please god no"
> · "fuck: my life"

and, separately:

> It works on my local machine (e.g. with `act`) but the real pipeline fails and the only
> way to change something is to make a commit. I am not a huge fan of hundreds of commits
> that are just something like "trying to fix the pipeline".

> The feedback loop is definitely up there. Waiting 6–10 minutes just to find out you
> missed a comma somewhere is painful.

22 of 96 comments touch YAML/syntax/debugging and 10 touch the local-repro loop, making
this the **dominant complaint on r/devops** — where it ranks above cost.

**Why this is ours and not a linter's.** Nobody can price this today. We can, exactly, with
data already ingested: a run whose commit touches *only* `.github/workflows/**`, in a
consecutive streak against the same branch, is config churn. Sum the billed minutes across
the streak and the finding writes itself — *"47 runs last month existed only to debug the
pipeline: 3.2 hours, $N. Here are the three workflows responsible."*

That is a report line no competitor is producing, it needs no new ingest, and it lands in
the Reddit audience's top complaint while being denominated in the HN audience's currency.

**Caveats, both real.** The saving is not recoverable by a YAML fix — the remedy is
`act`, better local validation, or pre-flight checks, none of which we ship. So this is a
*measurement* finding, not a fixer, and it collides with the same
replay-vs-projection question as P2-A. It may belong in the audit report's context section
rather than the findings list. Second, attribution needs care: a workflow edit bundled with
code changes is ordinary work, not churn. The signal is **workflow-only commits in
consecutive streaks**, and the streak length is what makes it churn rather than maintenance.

- Detector: `pipeline_fix_churn`
- Evidence: commit → files changed → run → billed minutes, grouped into per-branch streaks

---

## Phase 3 candidates — failure → taxonomy → signature → blame

### P3-A · Exit 137 is two different failures sharing one exit code

The F1 taxonomy lists OOM as a single class. That is too coarse, and being wrong here
actively misleads.

An [active community discussion](https://github.com/orgs/community/discussions/169191) on
`ubuntu-24.04` documents jobs receiving SIGKILL **from the host**, while the container's own
diagnostics show no pressure at all: ~500 MB used, 1.8 GB free, nothing in `dmesg`, no CPU
throttling. The tell is zombie processes immediately before termination — an abrupt external
kill, not the guest OOM killer.

- **Guest OOM** → "your build needs more memory" → raise limits, split the step.
- **Host eviction / noisy neighbour** → "this is infrastructure" → retry, or move the job.

Same exit code, opposite remediation. Classifying both as OOM sends developers on a memory
hunt for a problem that is not theirs. This is precisely the misattribution F1 exists to
prevent, and it is a taxonomy refinement rather than new machinery.

### P3-B · Retry-to-green is a gold label we already own

The Phase 0 post-ship audit fixed the bug where "a re-run's earlier attempts were never
ingested — dropping exactly the failing jobs that are Phase 3's gold labels." That data is
in the DB now.

A same-commit re-run that flips fail → pass is the strongest flakiness label obtainable
**without instrumenting anyone's test framework**. It costs nothing and needs no
cooperation from the repo. Worth naming explicitly as the bootstrap label so the 300-log
golden corpus is not the sole path to a usable classifier, and so week 14's first task
(gold-label count) has a second source to cross-check against.

### P3-C · Attribution decides who gets paged

The triage literature converges on classifying each failure as product bug / flaky /
environment / drift, so that "the right person is looped in rather than everyone guessing"
— turning a wall of failures into a short list of decisions. Adjacent to F4 blame but
cheaper: routing to the right *category* does not need blame-candidate precision.

### P3-D · Platform incidents are not the developer's fault, and we can prove it

**New candidate, and the single highest-scoring comment in the r/devops thread** (40 points,
roughly double the next reply) is this:

> They're at an amazing 92.7% uptime. They can't even keep 2 nines of uptime.

The figure is the commenter's claim and is not independently verified here, but the
underlying dataset is real, public and open source:
[`mrshu/github-statuses`](https://github.com/mrshu/github-statuses) reconstructs
per-component GitHub uptime by replaying snapshots of the **public GitHub Status Atom
feed** (`history.atom`). That feed is free, historical, and directly consumable.

The F1 taxonomy lists "external service" as a class. This says the most important external
service is **GitHub itself**, and that it is uniquely attributable: when Actions is in a
declared incident window, jobs fail for reasons that have nothing to do with the commit
under test. Every other failure class we assign is a claim about the developer's code or
config. This one is a claim about the platform, and it is the only class we can establish
from an authoritative third-party record rather than by inference from logs.

Cheap to build, high credibility, and it protects the classifier's precision: failures
inside a declared incident window should be excluded from flake statistics entirely rather
than diluting them. Note that HN corroborates the theme independently — GitHub Actions
outages account for two of the six highest-scoring Actions stories in the sample
(655 and 509 points).

- Taxonomy class: `platform_incident`
- Evidence: job failure timestamp ∩ GitHub Status incident window for the Actions component

---

## Competitive and positioning notes

Not candidates, but they change how candidates should be framed.

**Someone is already writing our catalog as prose.** A commenter promoting
[costops.dev](https://costops.dev/guides) summarises the fixes for slow/expensive GHA as:

> caching, tuning what runs on each push, separating out unit tests vs e2e, separating test
> from build.

Two of those four are shipped Cadence rules and one is planned path-trigger work — external
confirmation that the catalog targets the right things. The fourth, **separating test from
build and unit from e2e**, is *not* in the catalog: it is a job-splitting recommendation
rather than a config toggle, and it sits near the class D/E long-tail work. Worth deciding
whether the catalog covers structural advice or only mechanical fixes.

**A vendor is already doing our arithmetic, for migration instead of optimisation.** A
Semaphore representative in-thread:

> same Rails app, 10 runs, matched hardware: Semaphore 5:01, GitHub Actions 9:44. At 100
> builds/day that's ~6.5 engineer hours lost daily just waiting.

Identical unit economics, opposite conclusion — their recommendation is *switch platforms*,
ours is *fix the pipeline you have*. Cadence's advantage is that the second requires no
migration; the risk is that the comparison makes the first look decisive. The audit report
should be able to answer "would switching beat fixing?" rather than leaving it implicit.

---

## Cross-cutting — workflow config is never persisted

Found in our code, not in the field. [`cli.py:381`](../../src/cadence/cli.py#L381) fetches
workflow files live at audit time. The schema is `repo` / `run` / `job` / `step` /
`log_chunk` / `finding` / `evidence` / `ingest_job` / `webhook_delivery` / `rate_card` —
there is **no config table**. Three consequences, all landing on Phases 2 and 3:

1. **No config history**, so "the workflow changed here and waste started" is unanswerable.
2. **Phase 2's round-trip ship criterion is not reproducible as written.** "200 corpus
   workflows, byte-identical when no fix applied" re-fetches from HEAD, so the corpus
   shifts under the test and a failure cannot be distinguished from an upstream edit.
3. **Phase 3 blame gets materially stronger** with "the workflow itself changed in this
   window" as a feature.

Small schema addition, and much cheaper before Phase 2 than retrofitted after.

---

## What the field does *not* complain about

Recorded because it cuts against our own roadmap, and negative results are worth as much as
positive ones.

**Flakiness is 15 of 1,546 HN comments and 6 of 96 Reddit comments. Matrix waste is 11 on
HN, 0 on Reddit.** Phase 3 is a seven-week investment aimed at flaky build intelligence;
both audiences are exercised about other things — cost and runner performance on HN,
debuggability and the edit-test loop on r/devops.

Worse than low volume, there is an explicit **scope rejection**. The r/devops OP listed
flaky tests as a candidate frustration, and a reply pushed back directly:

> "Flaky tests that pass 'most of the time' and constant re-running by dev teams" — A Dev
> team problem, not CI/CD.

That is one commenter, lightly upvoted, and it is not evidence that flakiness is unimportant.
It *is* evidence that some practitioners do not consider it their CI tool's job to solve —
which is a positioning problem for Phase 3 independent of how good the classifier is.

Three honest readings, and they are not mutually exclusive:

1. **Sampling bias.** All three threads were framed around pricing and general frustration.
   A thread about testing would invert these numbers, and the academic literature is clear
   that flakiness is widespread — 59% of developers hit it at least monthly, and one mobile
   CI dataset put teams experiencing flakiness at 26% in 2025, up from 10% in 2022.
2. **Flakiness is suffered privately.** Cost arrives as a bill someone has to defend in
   public; flakiness arrives as a re-run click, which is annoying but individually cheap.
   Low complaint volume is not low incidence.
3. **Or Phase 3 is genuinely further from the market's felt pain than Phases 1–2**, in which
   case the ordering deserves a second look before seven weeks are committed.

This does not settle anything, and it should not be used to justify cutting Phase 3. It does
mean **the Phase 3 kill criteria deserve to be taken literally** rather than treated as
formalities, and that the debuggability angle (229 comments) may be the more
commercially-loaded half of the same data.

---

## Ranking

| # | Item | Type | Why here |
|---|---|---|---|
| 1 | **C0** rate card stale | Bug, shipped code | Under-reports savings for the exact audience most likely to buy; rate-card versioning already exists to absorb this |
| 2 | **P2-A** `no_job_timeout` | New fixer | Uses data we already hold; exact fixer shape; ~4 in 5 corpus jobs unprotected |
| 3 | **P2-D** pipeline-fix churn | New detector | Dominant r/devops complaint; nobody prices it; needs no new ingest |
| 4 | **Config persistence** | Schema | Unblocks a Phase 2 ship criterion; cheapest now |
| 5 | **P3-D** platform incidents | Taxonomy | Top-voted thread comment; free authoritative source; protects classifier precision |
| 6 | **P3-A** exit-137 split | Taxonomy | Sharpens F1 where being wrong misleads |
| 7 | **P2-B** cache eviction | New detector | Natural sibling of a shipped rule |
| 8 | **P3-B** retry-to-green | Labelling | Mostly making explicit what the data already supports |
| 9 | **P2-C** version rot | New fixer | Real, but Dependabot-adjacent and zero corpus instances |

Items 1–4 need no new data source. Item 5 needs one public feed. That grouping, rather than
the strict ordering, is the useful decision boundary.

---

## Sources

**Primary — GitHub**
- [2026 pricing changes for GitHub Actions](https://github.com/resources/insights/2026-pricing-changes-for-github-actions)
- [Actions limits](https://docs.github.com/en/actions/reference/limits)
- [Dependency caching reference](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching)
- [Cache size can now exceed 10 GB](https://github.blog/changelog/2025-11-20-github-actions-cache-size-can-now-exceed-10-gb-per-repository/)
- [New date for enforcement of cache eviction policy](https://github.blog/changelog/2025-09-29-new-date-for-enforcement-of-cache-eviction-policy/)
- [`runner-images#14254` — Ubuntu 22 deprecation](https://github.com/actions/runner-images/issues/14254)
- [Cache eviction does not appear to be LRU](https://github.com/orgs/community/discussions/156773)
- [Exit code 137 on ubuntu-24.04](https://github.com/orgs/community/discussions/169191)

**Developer threads**
- [r/devops — What's your biggest frustration with GitHub Actions (or CI/CD in general)?](https://www.reddit.com/r/devops/comments/1rdrpzz/whats_your_biggest_frustration_with_github/) — 2026-02-24, score 55, 96 usable comments (retrieved via [Arctic Shift](https://arctic-shift.photon-reddit.com))
- [`mrshu/github-statuses`](https://github.com/mrshu/github-statuses) — historical GitHub component uptime from the public status Atom feed
- [Pricing Changes for GitHub Actions](https://news.ycombinator.com/item?id=46291156) — 802 pts, 819 comments
- [The Pain That Is GitHub Actions](https://news.ycombinator.com/item?id=43419701) — 704 pts, 562 comments
- [I hate GitHub Actions with passion](https://news.ycombinator.com/item?id=46614558) — 490 pts, 341 comments

**Secondary**
- [Use GitHub Actions timeouts to protect your budget](https://emmer.dev/blog/use-github-actions-timeouts-to-protect-your-budget/)
- [GitHub Actions job timeout](https://fixdevs.com/blog/github-actions-timeout/)
- [Flaky test benchmark 2026](https://testdino.com/blog/flaky-test-benchmark)
- [AI test failure triage 2026](https://qaskills.sh/blog/ai-test-failure-triage-auto-tfa-2026)
- [LLM-driven CI failure diagnosis](https://www.researchgate.net/publication/401215124_LLM-Driven_CI_Failure_Diagnosis_and_Automated_Repair_From_GitHub_Actions_Logs_to_Patch_Recommendation)
