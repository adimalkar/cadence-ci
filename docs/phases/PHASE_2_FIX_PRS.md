# Phase 2 — Remediation Pull Requests

**Weeks 11–13 · Turns diagnosis into a merge button · Résumé line lands here**

---

## What this phase delivers

For findings with a safe deterministic fix, Cadence opens the pull request that applies
it, with the simulated saving in the description and the evidence linked.

```
cadence: cache Node dependencies in ci.yml

Adds actions/cache to the `test` job. Currently `npm ci` runs cold on
every execution — 4.2 min of the job's 5.1 min average.

Simulated: 4.2 min/run saved (projection, based on 340 comparable
Node projects — range 3.1–4.8). At your current 220 runs/month on
ubuntu-latest 2-core, ~$5.50/month.

Evidence: 1,412 runs analysed · cache step absent since 2024-03-11
Verify: [full analysis]
```

This is the step that converts Cadence from a report into a tool. Adoption cost drops from
"read a dashboard and do the work yourself" to "review a diff and merge."

---

## Why this is the phase that makes the product

Every competitor in the space stops at the report. Kleore produces a prioritized fix list;
CICosts produces dashboards; Datadog produces critical-path views. **The work of actually
changing the YAML is left to a human in all of them.**

That gap is where adoption dies. A maintainer who agrees with the finding still has to
find the file, remember the cache action's syntax, get the key composition right, and
verify it worked. Most never do. The finding was true and nothing happened.

A PR collapses that to thirty seconds — and it is also how we get **calibration ground
truth**, because a merged fix gives us before/after run durations for the same repo. No
other phase produces that.

---

## Prior art

Automated PR generation is a solved problem with well-known failure modes. We should copy
the solutions, not rediscover them.

### [Renovate](https://github.com/renovatebot/renovate) — the model to follow

The most battle-tested mass-PR bot in existence, and its hard-won design decisions are
directly applicable:

- **Concurrency limits** (`prConcurrentLimit`, `prHourlyLimit`) — the single most important
  anti-spam control. Without it a first run on a stale repo opens forty PRs and gets the
  app uninstalled the same day.
- **Grouping** — related changes land in one PR, not N.
- **A dashboard issue** — one pinned issue listing everything the bot *could* do, from
  which the user opts in. This is a far better first-contact pattern than opening PRs
  unprompted, and it maps perfectly onto our audit report.
- **Never reopen a closed PR.** A closed Cadence PR is a permanent suppression signal for
  that finding. Wire it to the `suppressed` status in the finding lifecycle.
- **Rebase strategy** — conflicts are inevitable; auto-rebase on a schedule, not on every
  push.

### [OpenRewrite](https://github.com/openrewrite/rewrite) / Moderne

Recipe-based deterministic source transformation with an auto-refactoring engine. Their
relevant idea is the **recipe as a first-class, testable, versioned artifact** — each
transformation is independently unit-testable against before/after fixtures. Our fixers
should be structured the same way: a fixer is a named, versioned object with a test corpus,
not a function buried in a detector.

Moderne also ships a pattern of annotating PRs with recipe fixes as a quality gate, which
is close to our check-run-plus-optional-PR surface.

### Sourcegraph Batch Changes

Large-scale changes across many repos, with a preview-then-apply workflow. Relevant mainly
as evidence that the "generate diff, review in bulk, then publish" model is what users
expect at scale.

### Verdict

**Nobody generates workflow-YAML performance fixes.** Renovate does dependencies,
OpenRewrite does source code, Batch Changes does bulk edits you author yourself. The
`.github/workflows/` performance surface is unclaimed.

---

## Which findings get a fixer

Not all of them. A fixer ships only when the transformation is deterministic and the blast
radius is understood.

| Finding | Fixer | Why |
|---|---|---|
| No dependency cache | ✅ | Well-known snippet per ecosystem; additive |
| Cache key thrashing | ✅ | Rewrite key to `OS + hashFiles(lockfile)` |
| `run_id` in cache key | ✅ | Unambiguous bug; single-line fix |
| No `concurrency` block | ✅ | Three lines, additive, no semantics change |
| Irrelevant path triggers | ⚠️ manual | Requires judgement about what "relevant" means |
| False `needs:` edge | ⚠️ manual | Removing a real dependency breaks builds |
| Non-discriminating matrix leg | ⚠️ manual | Coverage decision belongs to maintainers |
| Runner resize | ⚠️ manual | Cost/perf tradeoff is the user's call |
| Long-tail tests | ❌ | Requires source changes, not config |

⚠️ findings still ship — as a finding with a *suggested* diff shown inline in the check
run, which the user can copy. We just don't open the PR.

The asymmetry is deliberate: **additive, reversible changes get automated; subtractive or
semantic ones get proposed.** A bad cache config wastes a minute. A wrongly removed
`needs:` edge ships broken code to production.

---

## Engineering

### YAML editing must preserve formatting

Round-tripping workflow YAML through a naive parser destroys comments, key order, quoting
style, and anchors — producing an unreviewable diff and an instant close.

Use a comment-preserving round-trip editor (`ruamel.yaml` in round-trip mode, or a
CST-level edit against the source text). **The diff must touch only the lines being
changed.** Add a test that round-trips 200 real workflow files from the corpus and asserts
byte-identical output when no fix is applied. Without that test this will regress silently.

### Fixer contract

```python
class Fixer(Protocol):
    id: str              # 'cache.node.npm'
    version: int         # bump invalidates prior PRs
    applies_to: str      # finding kind
    def preview(self, repo, finding) -> Diff | None: ...
    def confidence(self, repo, finding) -> float: ...
```

`preview` returns `None` rather than guessing when the workflow shape is unfamiliar.
Declining to fix is always correct; a wrong fix is not.

### Permissions

This is the first phase requiring `pull_requests:write` and `contents:write`. Both are
**opt-in at install time, separately from the read scopes.** A user can run the audit
forever without granting write. Preserve that — it is the whole no-commitment thesis.

### Verification loop

After a Cadence PR merges, watch the next 30 days of runs on that ref and write
`realized_dollars_per_month` / realized minutes back to the finding. That number feeds
§9 calibration and, aggregated across installs, becomes the cross-repo fix-effectiveness
prior — the second-order moat.

---

## Anti-spam rules (non-negotiable)

1. **Max 1 open Cadence PR per repo at a time**, until a first one merges. Then max 3.
2. **The audit report comes first, always.** PRs are offered from the report, never
   pushed cold. Renovate's dashboard-issue pattern, adapted.
3. **A closed PR permanently suppresses that finding** at `rule_repo` scope.
4. **No PR on repos with no prior interaction** — never open an unsolicited PR on a repo
   we ingested read-only. That is a fast route to being labelled a spam bot across the OSS
   community, and the reputational damage is not recoverable.
5. **One fix per PR.** Grouping is right for Renovate because dependency bumps are
   homogeneous; our fixes have different risk profiles and reviewers need to judge them
   separately.

Rule 4 deserves emphasis. The no-install corpus exists to build and evaluate detectors,
and to generate reports we offer *in an issue, when invited*. It does not license writing
to 50 repos.

### Rule 3 is currently unimplementable — fix that before the first PR

**Found 2026-09-03.** Rule 3 says a closed PR permanently suppresses that finding at
`rule_repo` scope. Nothing in the codebase can do that.

The schema is ready and has been since migration `001`: `finding.status` takes
`'suppressed'`, `suppress_scope` takes `rule_repo`, and `suppressed_by` / `suppressed_reason`
are there to record who and why. [`findings.py`](../../src/cadence/findings.py) already
preserves a suppression across re-audits and marks a returning finding `regressed`. The
`dedupe_key` design comment states the intent outright — waste findings key on
`(rule, workflow_path, job_name)` *"so editing the YAML does not orphan a suppression."*

**Nothing writes the column.** There is no ignore file, no inline comment, no CLI verb, no
API. Every piece of the mechanism exists except the one a user touches.

The consequence is exactly the failure this section is written to prevent: a maintainer closes
a Cadence PR, and the next audit proposes it again, and the one after that. Rule 1's
open-PR cap slows the rate; it does not stop the loop. A bot that re-asks a settled question is
a bot that gets muted, and rule 4 explains why that damage does not come back.

**Sequencing:** ship suppression before the first fixer, not alongside it. It is one parser,
one CLI verb and one `UPDATE` — cheap now, and expensive after the first maintainer has been
asked twice. Design is **F12** in [`FEATURE_CANDIDATES.md`](../FEATURE_CANDIDATES.md); the
shape is borrowed from Infisical's `.infisicalignore` plus inline `infisical-scan:ignore`.

Two rules to settle while it is cheap: a **reason is mandatory** (a suppression without one
becomes a permanent mystery, which is what `suppressed_reason` exists to prevent), and
suppression is **per-rule, never global** — a blanket mute is indistinguishable from
uninstalling, and it hides the signal that a rule is miscalibrated.

---

## Ship criteria

1. Fixers for the four ✅ rows, each with a before/after test corpus of ≥20 real workflows.
2. Formatting round-trip test green on 200 corpus workflows.
3. **≥5 Cadence PRs merged in repos we don't own.**
4. Realized-vs-predicted savings recorded for every merged PR.
5. Zero PRs opened without a prior invitation.
6. **A closed Cadence PR suppresses its finding, verified by test** — re-run the audit and
   assert the finding does not return. Rule 3 is only real when a test says so.

Criterion 3 is the résumé line. "I built a tool that analyzes CI" is a project; "maintainers
of repos I don't own merged my bot's PRs and their builds got faster" is a product.

---

## Risks

**Spam reputation.** The failure mode is irreversible and community-wide. Every rule in
the anti-spam section exists because some bot learned it the hard way. Start with limits
tighter than feel necessary.

**A fix that breaks a build.** Additive-only for automated fixes contains this, but a cache
action with a wrong key can still mask a stale dependency. Every fixer PR should carry a
one-line revert instruction.

**Formatting churn.** The most likely cause of a well-founded PR being closed unmerged is
a diff that touches 200 unrelated lines. This is why the round-trip test is a ship
criterion and not a nice-to-have.

---

# Execution checklist

Moved from `ROADMAP.md` 2026-08-30.

## Core

- [ ] Comment-preserving YAML round-trip editor
- [ ] **Round-trip test: 200 corpus workflows, byte-identical when no fix applied**
- [ ] Fixers: `cache.*`, `cache.key`, `cache.run_id_bug`, `concurrency.cancel`
- [ ] `preview()` returns `None` on unfamiliar shapes — declining is always correct
- [ ] Opt-in `pull_requests:write` / `contents:write`, separate from read scopes
- [ ] Anti-spam: 1 open PR max → 3 after first merge; report-first; closed = suppressed;
      **never an unsolicited PR on a read-only-ingested repo**
- [ ] **Suppression a user can reach — ship before the first fixer.** `.cadenceignore`,
      inline `# cadence:ignore <rule_id> — <reason>`, and `cadence suppress/unsuppress`.
      Reason mandatory; per-rule scope only, never global. The four schema columns exist
      and nothing writes them, so rule 3 above cannot currently be honoured (F12)
- [ ] Realized-savings writeback (30-day post-merge window)

**Prerequisite now satisfied.** The round-trip criterion was not reproducible while config
was re-fetched from HEAD — the corpus shifted under the test, so a failure could not be
distinguished from an upstream edit. Migration `005` and `configstore.py` pin it. Build the
round-trip test against `load_latest()`, not against a live fetch.

## F2 — findings console (weeks 12–13)

- [ ] Authenticated list: filter, sort, suppress with a reason
- [ ] "Open fix PR" from a finding
- [ ] **"This was wrong" button, prominent not buried** — it is the continuous eval stream
      that makes the calibration dashboard possible

## New fixers from field research

Evidence in [`PHASE_2_3_CANDIDATES.md`](PHASE_2_3_CANDIDATES.md).

- [ ] **`no_job_timeout` → `timeout.add`.** GitHub's default job timeout is 6 hours; a hung
      job bills silently until killed. Corpus sample: ~145 job blocks, 31 `timeout-minutes`
      declarations — **four in five unprotected.** A linter says "add a timeout"; we hold
      p99 step timings and can say *"your p99 is 4m12s across 87 runs; set 15."*

      **Render decision, settled 2026-08-29:** the saving is contingent — it materialises
      only when a hang occurs, which fits neither of §6's classes. Resolution: **quote
      dollars only with evidence.** A historical hang in the ingested window is priced as a
      replay saving from real burned minutes; with no hang on record the finding reports
      exposure in hours-at-risk and **no money**. No third render class.

- [ ] **`cache_evicted_before_reuse`.** Sibling of the shipped `cache_key_never_hits`: the
      key is right, the entry was evicted first. GitHub's own docs name this "cache
      thrashing"; the eviction sweep moved from daily to hourly. Evidence is hit-rate decay
      and total footprint against the 10 GB ceiling; the fix is scope reduction, not a key
      change.

- [ ] **`pipeline_fix_churn`.** Workflow-only commits in consecutive streaks, summed as
      billed minutes — *"47 runs last month existed only to debug the pipeline."* The
      dominant r/devops complaint, and nobody prices it. **Measurement finding, not a
      fixer** — the remedy is `act` or pre-flight validation, neither of which we ship — so
      it may belong in the report's context section rather than the findings list.

**Considered and ranked last: action/runner version rot.** Real and dated (`ubuntu-22.04`
brownouts 2026-09-17, retirement 2027-04-17) but a scan of 55 corpus repos found **zero
exposed**, and Dependabot and Renovate already own the ground. Recorded so it is not
re-litigated.

## Ship criteria

- [ ] Each fixer has a ≥20-workflow before/after corpus
- [ ] Round-trip test green
- [ ] **≥5 Cadence PRs merged in repos we don't own**
- [ ] Realized-vs-predicted recorded for every merged PR
- [ ] Zero uninvited PRs

## The prerequisite this phase does not have

**PR → run linkage does not exist.** Nothing joins a pull request to the runs it caused.
That blocks realized-savings writeback from attributing correctly, and it is the same
missing piece that blocks PR impact analysis (`FEATURE_CANDIDATES.md` F3) and stacked-PR
detection (5B). One unglamorous piece of work unblocks three features — do it early in this
phase rather than working around it three times.
