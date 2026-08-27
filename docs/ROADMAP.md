# Cadence — Execution Checklist

Design lives in [`PRODUCT.md`](PRODUCT.md) and the [`phases/`](phases/) docs. This file is
the checklist. 24 weeks, ~15 hrs/week. Tick items as they land.

**Slip rule:** any phase over 150% of budget gets cut to its deterministic core. Do not
extend.

**Field research, 2026-08-26** — [`PHASE_2_3_CANDIDATES.md`](phases/PHASE_2_3_CANDIDATES.md)
carries the evidence behind the items added below: 1,546 HN comments, the r/devops
frustration thread (96 comments), primary GitHub pricing and deprecation sources, and scans
of our own corpus. Two things in it change existing plans rather than adding to them — the
rate card is wrong in shipped output (Phase 1), and both audiences rank flakiness far below
cost and debuggability (Phase 3).

---

## Phase 0 — Ingest platform · weeks 1–3

*No user-facing surface. Starts the history clock — the only irreversible deadline in the
plan (GitHub retains logs 90 days).*

- [x] `git init`, Apache-2.0 LICENSE, README with the §1 thesis
- [x] Project skeleton, `pyproject.toml`, dependency lock
- [x] Postgres schema: `repo`/`commit`/`run`/`job`/`step`/`log_chunk`
- [x] `finding` + `evidence` with the evidence `CHECK` and the insert trigger
- [x] `CIProvider` protocol; GitHub Actions the only implementation
- [x] Run + job + **step-timing** ingest with ETag conditional requests
- [x] Rate-limit handling: `Retry-After`, secondary limits, backoff
- [x] Log fetch + gzip to object store (content-addressed, fetched once ever)
- [x] Webhook receiver: HMAC-SHA256, `X-GitHub-Delivery` idempotency, 200 in <500ms
- [x] Job queue on Postgres (`FOR UPDATE SKIP LOCKED`)
- [x] 50-repo corpus ingesting on a 30-min delta poll (51 repos seeded and backfilled
      cleanly, 0 errors, ~2 min at concurrency 5; recurring re-enqueue proven — see
      note below)
- [x] `docs/HELDOUT.md` — 15 repos, never looked at again

**Ship criteria**
- [x] 1,000 webhook deliveries → zero drops, zero duplicates (replayed against the real
      ASGI app + Postgres, ~30% redeliveries interleaved — GitHub's own redelivery UI
      needs a deployed public endpoint this environment doesn't have; also verified once
      over live HTTP with real HMAC signatures)
- [x] Full backfill of a ≥500-run repo without tripping a rate limit
      (`react/react`, 500 runs / 7,524 jobs / 92,269 steps, 2:06)
- [x] Step timings reconstruct their span within 2% (0.03% mean on 1,401 ruff jobs, 0.09%
      on 266 prettier, 0.04% on 7,110 react jobs)
- [x] 50 repos ingesting continuously — demonstrated live, not just by structure: an
      unattended worker crossed a real 30-minute interval boundary, processed all 51 corpus
      repos in an 80-second burst with zero re-seeding, and rescheduled every one correctly
      (`poll_repo` done 211→262, +51 unattended; `AUDIT_PHASE_0.md` Phase 5)

**What's still not proven:** the mechanism firing correctly across one interval boundary is
not the same as a deployment running for weeks unattended. That still needs a long-lived
process (systemd unit, container, etc.) outside any single verification session's scope —
see the top pre-Phase-1 item below.

**Post-ship audit — 12 bugs found and fixed.** See [`AUDIT_PHASE_0.md`](AUDIT_PHASE_0.md).
Four were silent data-fidelity losses that shipped under a green 66-test suite, the worst
being that a re-run's earlier attempts were never ingested — dropping exactly the failing
jobs that are Phase 3's gold labels (verified: 6 recovered from a single DRF run). Suite is
now 82 tests. **Deploying a long-lived worker is now the top pre-Phase-1 task**, since
every day without one is a permanently lost day of history.

---

## Phase 1 — Waste audit · weeks 4–10

*The product. First user-facing surface.*

**Weeks 4–5 — prove the path on three rules before widening**
- [x] `no_dependency_cache` · `no_run_cancellation` · `false_needs_edge`
      (+ `cache_key_never_hits`, which fell out of the cache rule for free)
- [x] finding → evidence → savings, end to end, persisted and idempotent
- [ ] …→ **check run** — needs App write scope; CLI + report is the current surface
- [x] Versioned rate-card table (`rate_card_version` stamped on every finding)

**Week 6 — the simulator**
- [x] Job DAG build + levelling from `needs:` (`dag.py`, cycle-safe)
- [x] Critical path vs wall-clock vs theoretical floor
- [x] Queue time computed separately (queue-bound repos get the opposite advice)
- [x] Replay engine over stored step timings (`simulate.py`)
- [x] Replay/projection kept structurally apart — no code path sums them

**Week 7** — catalog classes A–C complete *(4 of ~14 rules done)*
**Week 8** — [x] cost model, two-currency reporting · [ ] start cold pitches
**Week 9** — classes D–E; [x] projection engine · [ ] corpus priors
**Week 10** — eval harness, calibration measurement

### Correctness bug in shipped output — the rate card is stale

**Do this before any Phase 2 fixer quotes a dollar figure.** Found 2026-08-26; detail in
[`PHASE_2_3_CANDIDATES.md`](phases/PHASE_2_3_CANDIDATES.md) §C0.

[`cost.py`](../src/cadence/cost.py) reasons that the self-hosted per-minute charge is "a
shelved self-hosted charge may yet return." It is not shelved — it took effect
**2026-03-01**. GitHub now applies a **$0.002/min Actions platform charge** to all
workflows including self-hosted; hosted rates fell up to 39% on 2026-01-01 (which the card
*does* reflect); public repos stay free; private-repo self-hosted minutes consume the free
quota.

- [ ] Add self-hosted / unknown-label rows at $0.002 with `free_on_public`; bump
      `rate_card_version`; re-run `evalsweep`
- [ ] Reconcile the two disagreeing fallbacks — `usd_per_minute` returns `0.0` for unknown
      labels while `hypothetical_dollars_per_month` uses `rates.get(label, 0.006)`, the
      hosted-Linux rate. **One runner, two prices, both able to appear in one report.**

Not hypothetical for the corpus: `depot-ubuntu-24.04-*`, `depot-ubuntu-22.04-*`,
`ubuntu-latest-8core`, `ubuntu-slim` and `codspeed-macro` are all in use and none are in the
card. The audience most likely to buy a minute-reduction tool is the one that moved to
self-hosted to escape per-minute billing; they now pay again, and today we quote them **$0**.
**The `rate_card_version` design is what makes this cheap — this is that decision paying
off.**

### New catalog candidate — pipeline-fix churn

- [ ] `pipeline_fix_churn` — workflow-only commits in consecutive streaks, summed as billed
      minutes. *"47 runs last month existed only to debug the pipeline: 3.2 hrs, $N."*

The dominant r/devops complaint (22 of 96 comments on YAML/debugging, 10 on the local-repro
loop), it needs **no new ingest**, and nobody else prices it. Caveat carried from the
research: it is a **measurement finding, not a fixer** — the remedy is `act` or pre-flight
validation, neither of which we ship — so it may belong in the report's context section
rather than the findings list. Same replay-vs-projection question as `no_job_timeout` below.

**Verified against real corpus data** (`cadence audit <repo> [--dry-run]`):
ruff, prettier, requests, flask, gin, django, numpy, vite, deno, cargo, terraform.
Findings are sparse (0–2/repo) with no misfires; the empty state reads
"No recoverable waste found. Pipeline is tight."

**Known limitation — reusable workflows.** `jobs.x.uses: ./.github/workflows/_build.yml`
renames runtime jobs to `x / <inner>`, matching nothing in the calling file. Config↔runtime
mapping coverage therefore ranges 18–100% across the corpus. Where coverage is <80% the
critical path is **withheld** rather than shown, because the wall-clock-vs-critical-path
gap would otherwise read as recoverable time when it is really unmeasured work.
Resolving this is the highest-value next task in Phase 1.

**F0 + F1 — the audit report (weeks 8–10)** · design in [`FRONTEND.md`](FRONTEND.md)

The report is not a nice-to-have at the end of the phase — it **is** the cold-pitch
artifact from §10, so it has to exist the week pitching starts.

- [x] F0: design tokens, both themes, in `report.py` (self-contained — no CDN, no JS)
- [x] F1: audit report — `cadence audit <repo> --html out.html`, single shareable file
- [x] Hero waterfall: actual vs floor, recoverable region hatched
- [x] **Replay renders solid + point value; projection renders hatched + range.** The
      credibility rule from `PRODUCT.md` §6 made visible — test-enforced in
      `test_report.py`, including that both can appear on one page and stay distinct.
- [x] Every finding row: claim · evidence chips · saving · basis · confidence
- [ ] Hover a job bar → queue time splits out as a leading segment *(needs the per-job
      waterfall; current hero is the run-level actual-vs-floor bar)*
- [x] Empty state as a real outcome: "No recoverable waste found… This pipeline is tight."
- [x] Mobile — single-column below 640px; report is 10KB with no external requests
- [x] Withholds the waterfall below 80% mapping coverage rather than implying the
      unmeasured gap is recoverable
- [x] `--json` twin for the read API and eval harness

**Ship criteria** — measured 2026-08-24 via `evalsweep.py` over the full corpus

- [x] **Audit runs across all 50 corpus repos unattended.** 50/51 analysed; the one skip
      is a repo with no workflow files, which is a correct outcome rather than a failure.
- [ ] **Median repo: ≥3 findings, ≥10% recoverable — FAILS.** Median 1 finding
      (mean 1.18, max 6, 22/50 repos find nothing); median 0.0% recoverable, mean 8.4%,
      9/50 repos at ≥10%. **Diagnosed, not mysterious — see below.**
- [x] **Replay reconstructs historical durations within 2%.** n=171 fully-mapped runs:
      mean error 0.48%, median 0.00%. All 25 runs over 2% are **1 second absolute** on
      ~44s runs — timestamp granularity, not model error. For runs ≥120s: 75/75 within
      2%, mean 0.10%.
- [x] Zero findings without evidence — DB trigger verified to reject an evidence-less
      finding through the Phase 1 write path, not just in unit tests.
- [x] Report renders at 375px (single-column grid); every number is real text, no
      canvas or image-only values. *Not yet checked with an actual screen reader.*
- [ ] **3 maintainers of repos we don't own confirm a finding surprised them** —
      **blocked on contacting humans.** Cannot be self-verified; needs the cold-pitch
      outreach from §10.

### Why criterion 2 fails, and what actually fixes it

Not the detectors, and not the premise. Two measured causes:

1. **Ingest depth is too shallow for the replay rules.** We hold ~78 runs per repo, but
   they fan out across ~11 workflow streams — **median 4 runs per workflow**, and only
   39 of 544 streams reach `false_needs_edge`'s `MIN_RUNS = 20`. Config analysis judges
   **518 of 1,502 `needs:` edges (34.5%) independent**, so candidates are plentiful; they
   simply lack the sample to replay. Fix: raise corpus ingest depth (`corpus seed
   --limit 200`), which spans several rate-limit windows and is a scheduling task, not a
   code change.
2. **Only 4 of ~14 catalog rules exist.** The rules that find *large* time — matrix-leg
   pruning, long-tail tests, runner fit, path-trigger waste — are classes C–E, still
   unbuilt. Current findings are 24 × `no_run_cancellation`, 4 × `cache_key_never_hits`,
   4 × `no_dependency_cache`, 1 × `false_needs_edge`.

The matching **kill criterion is scoped to week 10 with the full catalog**, so this is not
a trigger yet — but it is the number to watch, and it should be re-measured after the
catalog is complete and ingest is deepened rather than assumed to improve.

---

## Phase 2 — Fix PRs · weeks 11–13

*Converts a report into a tool. Résumé line lands here.*

- [ ] Comment-preserving YAML round-trip editor
- [ ] **Round-trip test: 200 corpus workflows, byte-identical when no fix applied**
      *(not reproducible as written — see the config-persistence prerequisite below)*
- [ ] Fixers: `cache.*`, `cache.key`, `cache.run_id_bug`, `concurrency.cancel`
- [ ] `preview()` returns `None` on unfamiliar shapes — declining is always correct
- [ ] Opt-in `pull_requests:write` / `contents:write`, separate from read scopes
- [ ] Anti-spam: 1 open PR max → 3 after first merge; report-first; closed = suppressed;
      **never an unsolicited PR on a read-only-ingested repo**
- [ ] Realized-savings writeback (30-day post-merge window)

**Prerequisite — persist workflow config.** [`cli.py`](../src/cadence/cli.py) fetches
workflow files live at audit time and the schema has no config table. Three consequences,
and the second is a ship criterion:

- [ ] Store workflow-file snapshots per run (schema addition)
- Without it, "200 corpus workflows, byte-identical" re-fetches from HEAD, so the corpus
  shifts under the test and a failure cannot be told apart from an upstream edit
- Without it there is no config history, so "the workflow changed here and waste started"
  is unanswerable — and Phase 3 blame loses a strong feature

Cheapest before Phase 2 starts, expensive to retrofit after.

**New fixers from field research** — evidence in
[`PHASE_2_3_CANDIDATES.md`](phases/PHASE_2_3_CANDIDATES.md)

- [ ] `no_job_timeout` → `timeout.add`. GitHub's default job timeout is 6 hours; a hung job
      bills silently until killed. Corpus sample: **~145 job blocks, 31 `timeout-minutes`
      declarations — four in five unprotected.** A linter says "add a timeout"; we hold p99
      step timings and can say *"your p99 is 4m12s across 87 runs; set 15."*
- [ ] `cache_evicted_before_reuse` — sibling of the shipped `cache_key_never_hits`: the key
      is right, the entry was evicted first. GitHub's docs name this "cache thrashing"; the
      eviction sweep moved from daily to **hourly**. Evidence is hit-rate decay and total
      footprint against the 10 GB ceiling, and the fix is scope reduction, not a key change.

**Decide before building `no_job_timeout`:** its saving is *contingent* — it only
materialises when a hang occurs. `PRODUCT.md` §6 admits exactly two render classes, replay
(solid, point) and projection (hatched, range). **Risk avoided is neither.** Either add a
third class or forbid quoting dollars unless a historical hang exists in the window. The
same question governs `pipeline_fix_churn`.

**Considered and ranked last: action/runner version rot.** Real and dated —
`ubuntu-22.04` brownouts begin 2026-09-17, retirement 2027-04-17 — but a scan of 55 corpus
repos found **zero exposed** (`ubuntu-latest` ×56, `ubuntu-24.04` ×2; the only 22.04 strings
are Depot's third-party labels, unaffected). Dependabot and Renovate already own this, and
the threads show pinning is contested rather than settled. Recorded so it is not
re-litigated.

**F2 — findings console (weeks 12–13)**
- [ ] Authenticated list: filter, sort, suppress with a reason
- [ ] "Open fix PR" from a finding
- [ ] **"This was wrong" button, prominent not buried** — it is the continuous eval stream
      that makes the calibration dashboard possible

**Ship criteria**
- [ ] Each fixer has a ≥20-workflow before/after corpus
- [ ] Round-trip test green
- [ ] **≥5 Cadence PRs merged in repos we don't own**
- [ ] Realized-vs-predicted recorded for every merged PR
- [ ] Zero uninvited PRs

---

## Phase 3 — Flaky build intelligence · weeks 14–20

*Four months of history already in the DB by the time this starts.*

- [ ] **Week 14 first task: query the corpus for gold-label count.** Expect ~30 reruns per
      1,000 builds, ~68% flaky. If under a few hundred, extend ingest before training.
- [ ] **Retry-to-green as the bootstrap label.** A same-commit re-run flipping fail → pass
      is the strongest flakiness signal obtainable *without instrumenting anyone's test
      framework*. The Phase 0 audit already restored the earlier-attempt ingest this
      depends on, so it costs nothing and gives week 14 a second source to cross-check the
      gold-label count against.
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
- [ ] F2 log normalization + signature + clustering (16–17)
- [ ] **Golden corpus: 300 real failure logs → expected signature**
- [ ] F3 classifier: GBT, calibrated (isotonic/Platt) (18–19)
- [ ] Bootstrap from published datasets (`flaky-build.github.io`) before own gold set matures
- [ ] Week 18 decision: embeddings-as-features only if they beat tabular on held-out repos
- [ ] F4 blame candidates — ≤3 or nothing (20)

**Ship criteria**
- [ ] Golden corpus green
- [ ] F1 covers ≥80% of non-test flaky failures
- [ ] ≥85% flaky precision on ≥10 held-out repos
- [ ] Calibration within ±10% per decile
- [ ] Blame ≥70% precision, or emits nothing

### Demand signal is weaker than this phase assumes — read before committing 7 weeks

Flakiness is **15 of 1,546 HN comments and 6 of 96 r/devops comments**; matrix waste is 11
and 0. Both audiences are exercised about other things: cost (288) and runner performance
(309) on HN, YAML/debugging (22) and the local-repro loop (10) on r/devops. There is also an
explicit scope rejection in-thread — *"Flaky tests… A Dev team problem, not CI/CD."*

Three readings, and they are not exclusive: (1) sampling bias, since those threads were
framed around pricing and general frustration, and the literature is clear that ~59% of
developers hit flakiness monthly; (2) flakiness is suffered privately as a re-run click
while cost arrives as a bill someone defends in public, so low complaint volume is not low
incidence; (3) Phase 3 is genuinely further from felt pain than Phases 1–2.

**This does not justify cutting Phase 3, and should not be used that way.** It does mean
the kill criteria above deserve to be read literally rather than as formalities, and that
the debuggability cluster — the largest signal in both datasets — may be the more
commercially loaded half of the same data. Phase 4's feedback-loop decomposition and
`pipeline_fix_churn` both sit in that cluster.

---

## Phase 4 — Observability + trust · weeks 21–24

- [ ] Flaky-cost report — including infra flake (21)
- [ ] Feedback-loop decomposition: push → queue → execution → post (21–22)
- [ ] Changepoint regression detection, not fixed thresholds (22)
- [ ] DORA — one page, four queries (23)

**F3 — trends (21–22)** · **F4 — public calibration (24)**
- [ ] F3: flaky cost over time, feedback decomposition, regressions with introducing
      commit linked. One screen, not a dashboard suite.
- [ ] F4: **public calibration dashboard** — predicted vs realized per rule, replay and
      projection reported separately, published whether or not it flatters us

**Ship criteria**
- [ ] A maintainer says a number surprised them
- [ ] Calibration dashboard live, updating weekly, unattended
- [ ] Changepoint finds the introducing commit for ≥5 known regressions
- [ ] Feedback decomposition sums to wall-clock within 5%

---

## Phase 5 — Merge readiness + grounded review · design in [`PHASE_5_REVIEW.md`](phases/PHASE_5_REVIEW.md)

**5A + 5B — weeks 14–16** (cheap, deterministic, no new infrastructure)
- [ ] Unresolved review threads via GraphQL; **live vs `isOutdated` bucketed separately**
- [ ] Check run `cadence/merge-readiness`, always `neutral` — never a gate
- [ ] Predicted merge conflicts from `/pulls/{n}/files` hunks — **no clone**; escalate to
      `git merge-tree` only if hunk overlap proves imprecise
- [ ] Lockfile semantic overlap (compare package entries, not text lines)
- [ ] Symmetric PR-pair dedupe key, or the same conflict reports twice
- [ ] **Stacked-PR detection** — match open PRs' `base.ref` against other open PRs'
      `head.ref`; one paginated call, **no clone**. GitHub surfaces this nowhere: the PR
      list gives no signal and reviewers get no warning they are mid-stack. Verified
      2026-08-26 — 8 stacks in 100 open `vercel/next.js` PRs including a 3-deep chain, 0 in
      go/k8s/pytorch/polars, so it is team culture rather than a universal.
      **Two guards are mandatory**: only same-repo branches can be a parent, and the
      default branch is never a parent — without both, a fork PR whose head branch is named
      `master` makes the detector label 96–99 of 100 PRs as stacked.
      **Ships in two units, and the first has no prerequisite beyond PR ingest:**
  - [ ] *(a)* The stacked badge + chain view. Needs only `/pulls?state=open`.
        Tool-agnostic, so unlike Graphite's it also covers hand-rolled stacks — and it
        serves the **reviewer**, who never chose the author's tooling.
  - [ ] *(b)* The CI findings, once PR→branch→run linkage exists: blame misattribution (a
        child's failure can originate in a parent's commits) and rebase churn (a merged
        parent retargets every descendant, so a 3-deep stack pays for CI 3+ times).
        (b) is the priced finding; **do not hold (a) for it.** Reasoning in
        [`EXPANSION.md`](EXPANSION.md) §3.3.

**5C — weeks 35–40** (BYO-key review; un-defers the KMS vault)
- [ ] Vault: envelope encryption, per-installation DEK under a KMS CMK, decrypt only in
      the worker, never returned over the API, logger redaction on the plaintext
- [ ] Rotation + revocation from day one
- [ ] **Silence rule enforced mechanically**: hunks with no Cadence evidence are omitted
      from the prompt entirely
- [ ] Prompt-injection defences: diff as delimited data, model output can trigger **no**
      action, structural validation before posting
- [ ] Hard cap 3 inline comments/PR; module defaults **off** when another review bot is
      detected; drop comments within ±5 lines of an existing bot comment
- [ ] Degrade to silence on provider failure — 5A/5B still ship

**Ship criteria**
- [ ] Lockfile conflict prediction ≥90% precision on 30 hand-labelled PR pairs
- [ ] **Zero** stored-key appearances in any log/API/error payload (test greps the plaintext)
- [ ] ≥20 hostile diffs produce no action and no reflected instruction text
- [ ] Every posted comment cites at least one piece of Cadence evidence
- [ ] With the provider hard-down, checks still post 5A/5B
- [ ] On a repo with CodeRabbit installed, the review module posts nothing

---

## Phase 6 — Security, AI-first · design in [`PHASE_6_SECURITY.md`](phases/PHASE_6_SECURITY.md)

**6A — weeks 17–19** (secrets; uses logs already stored)
- [ ] CI-log secret scanning — the surface no scanner covers
- [ ] `.ipynb` **output** scanning (secrets live in committed notebook JSON)
- [ ] Verified liveness against provider token-info endpoints (~60% → ~99% precision)
- [ ] `dedupe_key = hash(detector_id, sha256(value))`; plaintext never stored/logged/rendered
- [ ] Finding links the provider's revoke page — rotation is the action, not detection

**6B — weeks 31–34** (the AI rule pack)
- [ ] Model output → `eval`/`exec`/shell/SQL/path dataflow rules
- [ ] Unsanitised prompt interpolation · tool handler without authz · unpinned weights ·
      MCP exposure · RAG injection surface · unvalidated structured output
- [ ] **MITRE ATLAS** technique IDs on every rule
- [ ] Published as an open versioned ruleset (`cadence-ai-security`)

**6C — weeks 41–46** (general SCA + reachability; un-defers the sandbox)
- [ ] OSV.dev + GitHub Advisory DB; CycloneDX SBOM
- [ ] Reachability with **suppression as the headline**
- [ ] Confidence tiers: `unreachable (high confidence)` vs `(dynamic dispatch — verify)`
- [ ] Sandbox: no egress, read-only root, tmpfs, cpu/mem/pid caps, non-root, seccomp.
      **Threat model written before the runner.**

**Ship criteria**
- [ ] ≥95% precision on live-secret findings
- [ ] Zero plaintext secrets anywhere, verified by test
- [ ] Notebook detection catches a planted secret source-only scanners miss
- [ ] AI rule pack ≥85% precision on held-out real AI repos
- [ ] **No `unreachable (high confidence)` finding is ever wrong** on the eval set
- [ ] Sandbox escape-attempt test passes

---

## What Phases 5 and 6 cost — read before committing

They roughly **double the plan: 24 weeks → ~46**, or about 11 months at 15 hrs/week. Two
specific costs beyond calendar time:

1. **Both deferred hard items come back.** The KMS vault (5C) and the sandbox (6C) were
   pushed out of Phase 0 precisely because they were "the two hardest and slowest items."
2. **A new class of risk.** Losing run history is embarrassing; leaking a customer's model
   API key is an incident with someone else's bill on it.

**The sequencing above is deliberate**, not the order the features were requested in:

- **5A/5B (wk 14–16) and 6A (wk 17–19) come early** — 6 weeks total, no new
  infrastructure, and 6A runs on logs already in the database. Highest value per week in
  the whole plan.
- **5C and 6C are last** — they carry the vault and the sandbox. Pulling 5C forward is
  possible, but it front-loads the hardest security surface before the product has users
  to justify it.
- The **résumé line stays at week 13**. Nothing here moves it; these extend the product
  rather than completing it.

If the plan slips, cut in this order: 6C → 5C → 6B. The deterministic wedges (5A, 5B, 6A)
are the ones that earn their weeks.

---

## Continuous — from week 1

- [ ] Ingest running (every day of delay is a permanently lost day of history)
- [ ] Eval harness written in Phase 1, reused by every later module
- [ ] Weekly publish: calibration, precision, n, date, detector SHA
- [ ] **Week 8: re-verify Kleore's feature set.** If they ship config rules + fix PRs, only
      the simulator differentiates and this plan needs revisiting.
- [ ] **Watch two more, found 2026-08-26.** [costops.dev](https://costops.dev/guides) is
      publishing our catalog as prose — "caching, tuning what runs on each push, separating
      unit tests vs e2e, separating test from build." Two of those four are shipped rules
      and one is planned path-trigger work, which is useful external confirmation; the
      fourth is **structural advice we do not cover**, and whether the catalog extends to
      job-splitting is an open call. Semaphore is running our arithmetic for the opposite
      conclusion — *"same Rails app, matched hardware: Semaphore 5:01, GitHub Actions 9:44…
      ~6.5 engineer hours lost daily"* — recommending migration where we recommend repair.
      **The audit report should be able to answer "would switching beat fixing?"** rather
      than leaving it implicit.

---

## Kill criteria

| Trigger | Action |
|---|---|
| <10% median recoverable wall time at wk 10 | Premise wrong. Stop before Phase 2. |
| Calibration <60% within ±25% at wk 13 | Drop dollar figures, ship raw diagnostics |
| <5 installs by wk 18 | Distribution problem. Stop building modules. |
| Flaky precision <75% at wk 20 | Deterministic core only, drop the classifier |
| Credential leak | All feature work stops until resolved and disclosed |

---

## Deferred (not cut)

**Promoted out of deferral on 2026-08-16** into Phases 5 and 6: secrets-in-CI-logs
(was [`EXPANSION.md`](EXPANSION.md) 1.1, now 6A), SCA/SBOM/reachability (was
[`PRODUCT_PLAN.md`](PRODUCT_PLAN.md) §8 and EXPANSION 2.5, now 6C), and static analysis
orchestration (now 6B/6C).

Still deferred, ranked in [`EXPANSION.md`](EXPANSION.md): preview-env cost attribution
(2wk), PR wait decomposition (2wk), test-impact advice (4wk).

Still explicitly rejected — see EXPANSION Tier 3 for the reasoning: merge queues, stacked-PR
**management**, preview-env provisioning, runner hosting, build caching, and per-person
velocity metrics.

**Narrowed 2026-08-26: stacked-PR *detection* is no longer rejected** and moved to 5B above.
The original entry rejected management and detection under one heading, on the grounds that
the category "shares no substrate" with us. 5B's no-clone PR-graph work makes that false —
detection is a cheaper query than the conflict prediction already committed to, and the CI
consequences of stacking (blame misattribution, rebase churn) are ours whether or not we
ever draw the stack. Graphite still owns the workflow product; we are not building a CLI. Note that **workflow security scanning** stays rejected even though Phase 6 adds
security: zizmor, poutine, and StepSecurity's Harden-Runner already own *workflow*
hardening, and Harden-Runner has an agent inside the runner with strictly more data than
our read-only position can reach. Phase 6 scans your **source and dependencies**, not your
CI configuration.
