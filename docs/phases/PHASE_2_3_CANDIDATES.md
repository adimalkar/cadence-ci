# Phase 2 / 3 — candidate problems from the field

Researched 2026-08-26. Candidates for the Phase 2 fixer catalog and the Phase 3 failure
taxonomy, drawn from primary GitHub sources, developer threads, and scans of our own
corpus. Design lives in [`PRODUCT.md`](../PRODUCT.md); the checklist is
[`ROADMAP.md`](../ROADMAP.md). This file is neither — it is a shortlist with evidence
attached, so the decision to build or drop each item can be made on numbers.

Nothing here is committed work. Ranking is at the bottom.

---

## Method, and what it could not reach

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
- **Our own corpus** — runner labels and `timeout-minutes` coverage sampled directly from
  the workflow files of corpus repos.

**Not reached: Reddit.** `r/devops` was requested as a source and is fully gated to this
environment — both the HTML and `.json` endpoints return an interstitial rather than the
thread. No Reddit content is represented below. If those threads matter, they need to be
pasted in by hand; nothing here should be read as covering them.

**Comment-volume distribution across the 1,546 HN comments**, which is the closest thing
to a demand signal available:

| Topic | Comments |
|---|---|
| runner performance / queue time | 309 |
| billing / minutes / cost | 288 |
| debugging + logs | 229 |
| timeouts / hangs | 143 |
| self-hosted + third-party runners | 129 |
| YAML / expressions / config | 115 |
| caching | 74 |
| flakiness / retries | 15 |
| matrix | 11 |

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

**Flakiness is 15 of 1,546 HN comments. Matrix waste is 11.** Phase 3 is a seven-week
investment aimed at flaky build intelligence; this audience is overwhelmingly exercised
about *cost* (288), *runner performance* (309) and *debuggability* (229) instead.

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
| 3 | **Config persistence** | Schema | Unblocks a Phase 2 ship criterion; cheapest now |
| 4 | **P3-A** exit-137 split | Taxonomy | Sharpens F1 where being wrong misleads |
| 5 | **P2-B** cache eviction | New detector | Natural sibling of a shipped rule |
| 6 | **P3-B** retry-to-green | Labelling | Mostly making explicit what the data already supports |
| 7 | **P2-C** version rot | New fixer | Real, but Dependabot-adjacent and zero corpus instances |

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
- [Pricing Changes for GitHub Actions](https://news.ycombinator.com/item?id=46291156) — 802 pts, 819 comments
- [The Pain That Is GitHub Actions](https://news.ycombinator.com/item?id=43419701) — 704 pts, 562 comments
- [I hate GitHub Actions with passion](https://news.ycombinator.com/item?id=46614558) — 490 pts, 341 comments

**Secondary**
- [Use GitHub Actions timeouts to protect your budget](https://emmer.dev/blog/use-github-actions-timeouts-to-protect-your-budget/)
- [GitHub Actions job timeout](https://fixdevs.com/blog/github-actions-timeout/)
- [Flaky test benchmark 2026](https://testdino.com/blog/flaky-test-benchmark)
- [AI test failure triage 2026](https://qaskills.sh/blog/ai-test-failure-triage-auto-tfa-2026)
- [LLM-driven CI failure diagnosis](https://www.researchgate.net/publication/401215124_LLM-Driven_CI_Failure_Diagnosis_and_Automated_Repair_From_GitHub_Actions_Logs_to_Patch_Recommendation)
