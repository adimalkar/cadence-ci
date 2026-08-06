# Cadence — Product & Engineering Plan

> Evidence-grounded CI and code intelligence. Every finding cites a file range or a log span.

*(Note: the project directory is spelled "Candence System" — likely a typo for "Cadence". Fix before it ends up in a repo URL.)*

---

## 1. Positioning

### The one-sentence pitch

> CodeRabbit reads your diff. Cadence reads your build history.

### Why this is not a fourth PR-review bot

CodeRabbit, Greptile, and Qodo are the same product with different context windows: ingest a diff, ask a model, emit comments. Their evidence class is **text the model read**. That market has three funded incumbents, a core that commoditizes every time a frontier model ships, and one remaining axis of competition (noise).

Cadence's evidence class is **execution that actually happened**. A test that failed on an unchanged tree. A CVE whose vulnerable symbol has no path from any entrypoint. A file that changed 34 times and caused 11 red builds. These are falsifiable claims backed by rows in a database, not model opinions — and producing them requires owning a CI ingest pipeline with months of accumulated history, which is infrastructure work rather than prompt work. That is the moat: not the code, the **accumulated run history and the priors derived from it**.

### Explicitly NOT the moat

- **Open source + BYO key.** Qodo's PR-Agent is already open source and BYO-key with a large install base. This is table stakes for OSS adoption. It is not a reason anyone picks you. Ship it; don't pitch it.
- **The unified `Finding` substrate.** This is an excellent *engineering* decision and a strong interview answer to "why one app instead of five." It is invisible to users. Users do not buy substrates. Keep it out of marketing copy and keep it in the architecture doc.

### Positioning relative to incumbents: complementary, not competitive

A repo should be able to run CodeRabbit **and** Cadence. Never ask anyone to rip out a tool they already like — that is the single biggest friction source in OSS adoption, and you have no leverage to win that fight.

**Where complementarity is structural (Phases 1–2):** Cadence's trigger is `workflow_run.completed`; every review bot's trigger is `pull_request.opened/synchronize`. Different moment, different evidence class, nothing to collide over. No incumbent does CI failure triage or build observability at all.

**Where it is not (Phases 3–4):** static analysis commentary on a diff is precisely CodeRabbit's and Greptile's territory. Running Semgrep on a PR and commenting about it puts you in the same place, at the same time, saying the same things. Coexistence in these phases has to be engineered; it does not come free.

### Universality is hygiene, not growth

Being compatible with everything removes an objection that would have killed an install. It does not create pull. Nobody adopts a tool because it coexists politely — they adopt it because the flaky-cost report told them something they didn't know. Build the compatibility, expect zero adoption lift from it directly, and treat it as insurance on installs you've already won.

The version that *does* create pull is one step further: **be an evidence source other tools can consume**, not merely a bot that stays out of the way. SARIF output, check-run annotations, a read API. That turns "complementary" from a manner into a capability, and it's the difference between a bot and a platform in an architecture interview.

### The three coexistence mechanisms

1. **Check Run as the primary surface, not PR comments.** The single most important decision here — see §6.6. The PR conversation timeline is the contested surface; the Checks tab is empty.

2. **Detect other review bots and defer to them.** `GET /repos/{o}/{r}/commits/{sha}/check-runs` exposes installed app slugs, and PR comments expose `user.type == "Bot"` — both available under permissions you already hold. If `coderabbitai[bot]` (or `greptile-apps[bot]`, `qodo-merge-pro[bot]`, …) is active on the repo, suppress generic lint/style findings entirely and surface only runtime-grounded ones. For line-level overlap, the deterministic rule is enough: if another bot has already commented within ±5 lines of your evidence range, drop yours.

   Nobody in this category does deference. It is ~2 days of work, it is a README screenshot, and it converts a claim about coexistence into a feature.

3. **SARIF from day one of Phase 3.** GitHub code scanning ingests it, so findings land in the Security tab for free and any other tool can consume them. An afternoon if built into the `Finding` serializer; a week if retrofitted.

### The universality axis that matters more: CI providers

Review-bot compatibility affects whether an install *survives*. CI-provider compatibility affects whether an install is *possible*. GitHub Actions, CircleCI, Buildkite, and GitLab CI are four different log and event models.

Build for GitHub Actions only — it dominates OSS CI and it is correctly the entire install target for the first 28 weeks. But put ingest behind a provider interface (`fetch_runs`, `fetch_logs`, `normalize_event`, `post_result`) so a second provider is a plugin rather than a rewrite. One hour of design discipline in week 1 against a month of refactoring in week 30.

### Competitive risk worth naming

CodeRabbit or Qodo could add flaky detection. Real, but slow: they would start from zero run history, and a diff-in/comment-out architecture is the wrong shape for time-series analysis over builds. Your history compounds from week 1 and cannot be bought back later — which is the concrete reason the no-install ingest (§2) starts immediately rather than after Phase 0 lands.

### The three cheap differentiators to build deliberately

1. **Public, continuously-updated precision dashboard.** Per-module precision on held-out repos, refreshed weekly, with the eval methodology published. No competitor does this. In a market whose universal complaint is noise, a live precision number is the strongest available trust signal — and it doubles as the best possible interview artifact.
2. **Suppression as a headline feature.** "CVE-2026-X: unreachable, no action needed." "40 lint findings in a file untouched since 2023: baselined." Telling a developer what they can safely ignore is rarer, more memorable, and more trust-building than another alert.
3. **Cost in hours and dollars.** "Flaky tests burned 41 CI-hours last week (~$380 in runner time)." This is the manager screen, and it is the reason an install survives past month one.

---

## 2. Scope decisions

### Cut: Phase 6 (merge conflict orchestration)

Highest risk, lowest frequency, shares almost no substrate with the rest of the system, and "orchestrate, don't resolve" — while the correct safety call — yields a feature that has no room in anyone's existing workflow. Cutting it frees ~4 weeks for reachability analysis, which is the second-strongest differentiator.

If you later want it back, the tractable slice is lockfile conflicts only (deterministic, parseable, genuinely annoying, and shippable in ~1 week as a standalone tool).

### Demote: Phase 5 (issue synthesis)

Not a phase. It's a button on the findings UI: "create GitHub issue from this cluster." One week, folded into Phase 4. Human-approval-gated forever, not just "until precision is proven."

### Promote: reachability

Move from a Phase 4 sub-feature to a first-class deliverable with its own ship criterion. It is the highest-value signal in the vulnerability space, most tools do it badly or paywall it, and you get most of the graph for free from Phase 3.

### Add: no-install read-only mode (week 2, non-negotiable)

Ingest public repos' run history through the GitHub REST API with a personal token, no App install required. This unblocks everything: you can build and evaluate the entire flaky classifier on real data before a single stranger trusts you, you can demo on repos you don't own, and you can pre-compute findings for a repo *before* pitching its maintainer.

**This is the highest-leverage item in the plan and it is two days of work.** Do not skip it.

---

## 3. Timeline (revised, honest)

Assumes ~15 hrs/week alongside a job search.

| Phase | Weeks | Cumulative | Deliverable |
|---|---|---|---|
| 0 — Platform | 1–5 | 5 | Ingest, storage, sandbox, vault, schema |
| 1 — CI triage | 6–12 | 12 | Failure clustering + flaky classifier + PR comment |
| 2 — Observability | 13–16 | 16 | DORA, cost/duration trends, flaky-cost report |
| **← résumé line goes here** | | **16** | **Portfolio-complete** |
| 3 — Audit + prioritization | 17–22 | 22 | Tool orchestration, churn/blast-radius ranking |
| 4 — Vulns + reachability | 23–28 | 28 | SCA, SBOM, reachability, secrets, issue button |

**~28 weeks, not 22**, and Phase 6 is gone. Your instinct to put it on the résumé early was right, but the honest line is week 16, not week 9 — Phase 1 is genuinely 6–7 weeks part-time because the ML has a data-collection tail that doesn't compress.

Slip discipline: if any phase runs >150% of its budget, cut the phase's scope to its deterministic core and move on. Do not extend.

---

## 4. Data model

### Entities

```
Installation ─┬─ Repo ─┬─ Commit ─┬─ Run ─┬─ Job ─┬─ Step ─┬─ LogChunk
              │        │          │       └─ Deployment
              │        ├─ File ─── Symbol ─── Edge (calls/imports)
              │        └─ Dependency ─── Advisory
              └─ Finding ─── Evidence
```

### The `Finding` table

```sql
CREATE TABLE finding (
  id                 uuid PRIMARY KEY,
  repo_id            bigint NOT NULL REFERENCES repo(id),
  module             text   NOT NULL,   -- 'triage'|'audit'|'vuln'|'secret'|'perf'
  kind               text   NOT NULL,   -- 'flaky_test'|'unreachable_cve'|...
  severity           smallint NOT NULL, -- 1..5
  confidence         real   NOT NULL,   -- 0..1, calibrated (see §8)

  dedupe_key         text   NOT NULL,
  fingerprint_v      smallint NOT NULL, -- lets you re-key without losing status

  status             text   NOT NULL,   -- see lifecycle below
  suppress_scope     text,              -- 'finding'|'rule_path'|'rule_repo'
  suppressed_by      bigint,
  suppressed_reason  text,

  first_seen_commit  text NOT NULL,
  last_seen_commit   text NOT NULL,
  first_seen_at      timestamptz NOT NULL,
  last_seen_at       timestamptz NOT NULL,
  resolved_at        timestamptz,

  title              text NOT NULL,
  suggested_action   text,
  llm_narrative      text,              -- nullable, NEVER load-bearing
  detector_version   text NOT NULL,

  UNIQUE (repo_id, dedupe_key, fingerprint_v)
);

CREATE TABLE evidence (
  id          uuid PRIMARY KEY,
  finding_id  uuid NOT NULL REFERENCES finding(id) ON DELETE CASCADE,
  kind        text NOT NULL,   -- 'code_range'|'log_span'|'run_history'|'graph_path'
  file_path   text,
  line_start  int,
  line_end    int,
  log_chunk_id bigint,
  byte_start  int,
  byte_end    int,
  run_ids     bigint[],
  payload     jsonb,
  CHECK (
    (kind='code_range' AND file_path IS NOT NULL AND line_start IS NOT NULL)
    OR (kind='log_span' AND log_chunk_id IS NOT NULL)
    OR (kind='run_history' AND array_length(run_ids,1) > 0)
    OR (kind='graph_path' AND payload IS NOT NULL)
  )
);
```

The `CHECK` constraint is the "no finding without evidence" rule enforced by the database rather than by convention. Add a trigger rejecting any `finding` insert that isn't followed by ≥1 `evidence` row in the same transaction. Make the rule structural — code review will not hold this line for 28 weeks.

### Status lifecycle

```
new → acknowledged → resolved
  ↓                     ↓
suppressed          regressed → acknowledged
```

`regressed` matters more than it looks: a finding that was fixed and came back is a *different, higher-severity* event than a new one, and surfacing it is a feature nobody else has because nobody else keeps finding identity stable across commits.

### Dedupe key design (get this right on day one)

The naive `hash(file_path, line, rule_id)` breaks on every line shift and destroys your suppression state. Key by **semantic identity**:

| Module | Dedupe key |
|---|---|
| Static analysis | `hash(rule_id, file_path, enclosing_symbol_qualified_name, normalized_snippet)` |
| CI failure | `hash(normalized_log_signature, job_name)` |
| Flaky test | `hash(test_suite, test_name)` |
| Dependency CVE | `hash(advisory_id, package_ecosystem, package_name)` |
| Secret | `hash(detector_id, sha256(secret_value))` — never the value itself |

`normalized_snippet` = the matched line with identifiers preserved and whitespace/comments stripped. Survives reformatting and line drift; changes when the code actually changes.

`fingerprint_v` lets you ship a better keying algorithm without orphaning every suppression a user has made. When you bump it, run a migration that maps old keys to new ones by best-effort match and carries `status` forward. You will need this at least twice. Budget for it.

---

## 5. Phase 0 — Platform (weeks 1–5)

**Deliverables**

- GitHub App, minimum permissions: `checks:read`, `actions:read`, `contents:read`, `pull_requests:write`, `metadata:read`. Nothing else. Every extra permission costs you installs.
- Webhook receiver: HMAC-SHA256 signature verification, `X-GitHub-Delivery` stored for idempotency, **respond 200 in <500ms and process async** (GitHub disables slow endpoints).
- Events: `workflow_run`, `workflow_job`, `check_suite`, `pull_request`, `push`, `deployment_status`.
- Job queue. Use Postgres (`SELECT … FOR UPDATE SKIP LOCKED`) — do not add Redis/Celery until you have a measured reason. One less thing to operate.
- Log/artifact store: S3-compatible (R2 or B2 — meaningfully cheaper egress than S3 for this workload). Gzip everything; CI logs compress ~20:1.
- **Sandbox.** You will execute untrusted code from strangers' repos. Non-negotiable: no network egress by default, read-only root FS, tmpfs workdir, CPU/mem/pid limits, wall-clock timeout, non-root uid, seccomp profile. gVisor or Firecracker if you can; a hardened container with `--network=none` at minimum. Write the threat model down before writing the runner.
- **BYO-key vault:** envelope encryption, per-installation DEK wrapped by a KMS CMK, decrypt only inside the worker process, never log, never return over the API even to the owner. Add a redaction filter on the logger keyed to the plaintext value as defense-in-depth.
- **No-install read-only ingest mode** (week 2 — see §2).

**Ship criterion:** install on a repo you don't control; 1,000 consecutive webhooks produce correct rows with zero drops and zero duplicates. Verify by replaying deliveries from GitHub's UI and diffing DB state.

**Risks**

- *Sandbox escape.* You are running arbitrary `npm install` from strangers. Treat every checkout as hostile. This is also the interview story worth the most — be able to describe your threat model in detail.
- *Rate limits.* 5,000 req/hr per installation. Log download is the hog. Cache aggressively, back off on 403 with `Retry-After`.

---

## 6. Phase 1 — CI failure triage (weeks 6–12)

The core of the product. Six weeks because the data pipeline has an unavoidable collection tail.

### 6.1 Log normalization → signature

The whole module's quality rests on this. Pipeline:

1. **Strip ANSI**, split into lines with byte offsets retained (you need offsets for `log_span` evidence).
2. **Locate the failure region.** Heuristics per framework, plus a generic fallback: the last N lines before the first non-zero exit, plus any block matching known assertion patterns.
3. **Normalize** — replace with stable placeholders: timestamps, durations, hex/UUIDs, absolute paths (keep the repo-relative tail), ports, PIDs, memory addresses, IP addresses, temp-dir names, line numbers *in vendor paths only* (keep them in first-party paths — they're signal).
4. **Extract signature** = the normalized assertion/error line + exception type + the top 3 first-party stack frames.
5. **Hash** it.

Build the framework-specific extractors for pytest, jest/vitest, go test, JUnit/Maven, cargo, RSpec first — they cover most of OSS. Generic fallback for the rest, tagged `low_confidence`.

**Test this like a compiler.** Golden-file corpus: ~300 real failure logs → expected signature. Every normalization change runs against it. Without this you will silently regress clustering and never notice.

### 6.2 Clustering

Exact signature hash first. Then near-duplicate merge via normalized edit distance / MinHash over the signature token stream, threshold tuned on the golden corpus. Do not use embeddings here — deterministic, cheap, explainable, and it's not the bottleneck.

### 6.3 Flaky classifier — and its data problem

Your stated labeling function (same commit, different outcome) is the strongest signal available, and it is **sparse**: most failures are never retried, so most examples are unlabeled. Plan for this explicitly.

Label sources, strongest to weakest:

1. **Same tree SHA, different outcome** — near-certain flaky. Rare. Your gold set.
2. **Retry succeeded, no intervening commit** — very strong. Requires `workflow_run` re-run events; capture `run_attempt`.
3. **Failed on a PR, same test green on the base branch at the same time** — strong.
4. **Test failed then passed with no change to any file reachable from it** (needs Phase 3's graph — retrofit later).
5. **Cross-repo prior**: this signature is flaky in ≥3 other repos. Powerful, hosted-only (see §9).

Features: historical fail rate for the signature, failure rate on unchanged trees, time-of-day/concurrency correlation, duration variance, whether the failure mentions timeout/network/port/race keywords, retry-success rate, number of distinct commits the signature spans, first-party-vs-vendor stack composition.

Model: **gradient-boosted trees** (LightGBM/XGBoost). Not a neural net. Tabular, small, explainable, trains in seconds, and you can show feature importances to a maintainer who asks "why do you think this is flaky" — which you will be asked constantly.

**Calibrate the output.** Isotonic regression or Platt scaling on held-out data, so `confidence: 0.85` means 85% of such findings are correct. An uncalibrated score is a lie you'll repeat in the UI.

### 6.4 Blame candidate

Deterministic: intersect files in the failing test's stack trace with files changed in the PR/commit range. Rank by (a) direct appearance in the trace, (b) `git log -L` recency on the specific lines, (c) churn. Emit ≤3 candidates with confidence, or emit nothing. **Nothing is a valid and often correct output** — a blame guess that's wrong twice destroys trust in the whole module.

### 6.5 The LLM's actual job

Detectors produce: signature, cluster, flaky probability, blame candidates, evidence spans. The model receives those as structured input and writes 2–4 sentences of prose. It never sees raw logs unfiltered, it never decides anything, and if the API call fails the comment still ships without narrative. **Hard-wire that fallback path in week 6**, not later — it's what makes "LLM is never the detector" true in the code rather than in the README.

### 6.6 Output surface — Check Run first

**Primary surface is a Check Run, not a PR comment.** Check runs render a markdown summary, carry native file/line annotations, appear in the Checks tab, and — critically — do not notify every subscriber or clutter the conversation timeline. The PR timeline is where every review bot competes and where maintainers' bot-noise complaints originate. The Checks tab is empty.

Rules:

- Check run named `cadence/triage`, `neutral` conclusion (never `failure` — you are not a gate, and becoming a red X on someone's PR is how you get uninstalled).
- Evidence spans become check-run annotations at the exact file/line.
- **At most one** PR comment, for the single highest-value finding only, edited in place across pushes — never a new comment per push. Default **off** when another review bot is detected (§1).
- Every claim links to its evidence.
- One-click "this was wrong" writing a labeled row to the eval set. That feedback loop is how the precision number in §10 becomes defensible, and it costs nothing.

**Ship criteria:** p95 <90s from `workflow_run.completed` to posted comment; ≥85% precision on flaky classification on ≥10 repos held out from development; golden corpus green.

---

## 7. Phase 2 — Observability (weeks 13–16)

Mostly aggregation over data you already have.

- DORA: deploy frequency, lead time for changes, change failure rate, MTTR — from `deployment_status` + run history.
- Per-workflow duration and cost trends; regression alerts on p50/p95 shifts (use a changepoint test, not a fixed threshold — fixed thresholds are noise machines).
- **"Top 10 flaky tests costing you N hours/week"** — with dollar conversion at GitHub's published runner rates. This is your best single screen. Lead the product with it.
- Public precision dashboard (§1.3) — build it here.

Ship criterion: a maintainer of a repo you don't own looks at the flaky-cost report and says a number surprised them.

---

## 8. Phases 3 & 4 — Audit and Vulnerabilities (weeks 17–28)

Condensed, since these are downstream and your original plan was already right about them.

**Phase 3 (17–22).** Orchestrate Semgrep, ESLint, ruff, gosec, govulncheck in the sandbox; normalize to `Finding`. **This is the phase where you overlap with the review bots** — apply the deference rules from §1 here specifically, and emit SARIF alongside the native surface so findings land in GitHub's Security tab and are consumable by anything else. The value-add is ranking: `score = f(churn_90d, blast_radius, recency, severity)` where blast radius = reachable-symbol count in the import graph. Baseline the existing codebase at install so only *new* debt surfaces. Build the import/call graph here — Phase 4 depends on it. Use tree-sitter for parsing; per-language resolvers for import edges.

Ship criterion: on a large unfamiliar repo, the top-10 list is defensible to its maintainer. Actually test this — DM three maintainers and ask.

**Phase 4 (23–28).** SCA against OSV.dev + GitHub Advisory DB; SBOM (CycloneDX). Then the differentiator: **reachability** — for each advisory with known affected symbols, search the call graph for a path from any entrypoint to that symbol. Report both directions, and make the negative the headline:

> `CVE-2026-1234` in `libfoo@1.2.3` — **not reachable**. No path from 14 entrypoints to `libfoo.parse_header`. Suppressed. [show analysis]

Be honest about limits: dynamic dispatch, reflection, `eval`, and DI containers defeat static reachability. Report `unreachable (high confidence)` vs `unreachable (dynamic dispatch present — verify manually)`. Overclaiming here is the one failure mode that could genuinely hurt a user.

Secrets: entropy + pattern + **verified liveness** (call the provider's token-info endpoint). Liveness verification takes precision from ~60% to ~99% and is the difference between a useful feature and a noise generator.

Fold in the issue-synthesis button: cluster → GitHub issue, deduped against open issues by embedding similarity, human-approval-gated permanently.

---

## 9. Open-core architecture (decide now, not in week 20)

BYO-key + self-hostable is right for adoption. It also means self-hosted installs generate no cross-repo data — which kills cross-repo signature priors, one of your best features. Design the seam deliberately:

| Open source (Apache-2.0) | Hosted only |
|---|---|
| All detectors, normalizers, parsers | Cross-repo flaky priors |
| Full schema, migrations, API | Public precision dashboard |
| Web UI, PR commenter | Managed ingest at scale |
| Self-host via docker-compose | Org-level DORA rollups |

Apache-2.0, not AGPL: AGPL scares off exactly the corporate users who'd become your reference installs, and your moat is data, not code.

Ship a **prior-sharing opt-in** for self-hosted: send anonymized signature hashes (no repo names, no log text, no code) and receive the global flaky prior in return. Some fraction will opt in, and it makes the network effect real instead of theoretical.

---

## 10. Measuring precision (the thing that makes the résumé line true)

Without this, "≥85% precision" is a wish.

- **Held-out repo set.** Choose 10–15 repos at week 1 and never look at their data while developing. Repo-level, not sample-level splits — same-repo leakage will inflate every number you report.
- **Labeling protocol.** For each module, sample 100 findings, label by a documented rubric, record inter-rater agreement with yourself at 2-week intervals (you will disagree with yourself; that's the point).
- **The in-product feedback button** (§6.6) is your continuous eval stream.
- **Publish it weekly.** Precision, recall where measurable, n, date, commit SHA of the detector version.

Write the eval harness in Phase 1 and reuse it for every later module. Doing it once is a week; retrofitting it four times is a month.

---

## 11. Distribution (start week 1, not week 16)

The hardest problem in the plan, and the one your draft covered in a sentence.

- **Weeks 1–12:** run in no-install mode against ~50 public repos with noisy CI. You need zero permission for this. Build the classifier on their history.
- **Week 13:** for 20 of them, generate a report *before* asking for anything. Open a well-written issue: "I analyzed your last 500 CI runs — these 6 tests cost you ~31 hours/month. Full data attached. No install needed; happy to keep it that way." Lead with the finished analysis, not the install link.
- Target repos with visibly painful CI: heavy integration suites, browser tests, anything with a `flaky` label already in use.
- **Conversion is going to be ~20–30%.** Pitch 60 repos to land 15 installs. Plan for the funnel, not the target.
- Keep every install running through week 28. **Four months of uptime while shipping migrations is the actual story** — the module list is just what you did.

---

## 12. Cost model

| Item | Monthly |
|---|---|
| VPS (4 vCPU / 8GB, workers + Postgres) | $25–40 |
| Object storage (R2, ~200GB compressed logs) | $5 |
| LLM (narrative only, ~4k tok/finding, Haiku-class for most) | $10–30 |
| Domain + misc | $5 |
| **Total** | **~$50–80** |

Keeping the model out of the detection path is what keeps this at $50 instead of $500 — that's the concrete payoff of the "LLM is never the detector" rule, and it's worth saying out loud in an interview. Cap per-installation spend and degrade to no-narrative mode when exceeded.

---

## 13. Kill criteria

Written now, while you're unattached to any of it.

- **Phase 1 flaky precision <75% on held-out repos at week 12** → ship the deterministic core only (clustering + signature history + "this signature failed on an unchanged tree 4 times"), drop the classifier. Still a good product.
- **<5 installs by week 20** → stop building modules. The problem is distribution, and a sixth module does not fix distribution.
- **Any module <80% precision at its phase end** → cut to deterministic core or cut entirely. Three modules at 90% beat six at 60%, as software and as a hiring signal.
- **Sandbox escape or key leak** → stop all feature work until resolved and disclosed. Non-negotiable.

---

## 14. Week 1 checklist

1. Rename the directory to `cadence-system`.
2. `git init`; Apache-2.0 LICENSE; README stating the thesis in §1.
3. Postgres schema from §4, with the evidence `CHECK` and the insert trigger.
4. GitHub App registered in dev mode, webhook receiver with signature verification + idempotency — with ingest behind a `CIProvider` interface (`fetch_runs`, `fetch_logs`, `normalize_event`, `post_result`), GitHub Actions as the only implementation.
5. Pick the 10–15 held-out repos. Write them into `docs/HELDOUT.md`. Never look at their data during development.
6. Start no-install ingest against 50 public repos — you want history accumulating from day one, because it's the one input you can't buy back later.
