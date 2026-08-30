# Phase 6 — Security: AI-First, Then General

**~13 weeks · AI-specific wedge leads; general SCA and reachability follow on the same
`Finding` substrate**

---

## Why AI-first is the right order

The generic version of this module — SCA against OSV, an SBOM, CVE lists — is well served
and has been since before Cadence existed. Snyk, Dependabot, Trivy, and GitHub's own
Advisory Database all ship it free or cheap. Entering there means competing on coverage
against companies whose entire product is coverage.

The AI-specific slice is different, and the reasoning that motivated this feature is the
correct one: **AI codebases are structurally leakier than the tools scanning them assume.**

- Model API keys are pasted into notebooks, `.env` files, and Gradio demos at a rate no
  other ecosystem matches.
- `.ipynb` files store **execution output**, so a printed key or token is committed *inside
  the notebook JSON* — and most scanners either skip notebooks or scan only their source
  cells.
- Agent and tool-calling code routinely passes model output to `eval`, `exec`, a shell, or
  a SQL string, which is remote code execution wearing a friendly name.
- Model weights are pulled by mutable tag with no revision pin — a supply-chain hole with
  no lockfile equivalent.
- MCP servers get stood up bound to `0.0.0.0` with no auth, because the tutorial did.

None of that is exotic. It is the default shape of a fast-moving AI repo, and the existing
scanner ecosystem was built for a different threat model.

**Positioning:** ship the AI rule pack as an **open, versioned ruleset**
(`cadence-ai-security`). It is a credibility artifact and a distribution channel — people
adopt rulesets far more readily than platforms — and it costs nothing to open, since the
moat is the run history, never the rules.

---

## 6A — Secrets, everywhere they actually leak (weeks 1–3)

Three surfaces, one detector. The first is already ranked #1 in
[`EXPANSION.md`](../EXPANSION.md).

| Surface | Why it matters | Served today? |
|---|---|---|
| **CI logs** | Retained 90 days, readable by anyone with repo read access — *everyone*, on a public repo. Echoed env vars, `set -x`, crash dumps with connection strings | No. Every scanner reads source and git history, not log output |
| **Notebook outputs** | `.ipynb` stores stdout inside the committed JSON; AI repos are notebook-heavy | Barely — most tools scan source cells only |
| **Source + git history** | The standard surface | Yes, well |

We already store every log line from every ingested run, so the highest-value surface is
nearly free.

### Precision comes from liveness, not cleverness

Entropy plus regex lands around 60% precision and generates exactly the noise this product
exists to avoid. **Verified liveness** — calling the provider's token-info endpoint to ask
whether the credential still works — takes that to ~99% and turns the finding from a guess
into a fact.

Providers worth verifying on day one: Anthropic, OpenAI, GitHub, AWS, Google Cloud, Slack,
Stripe, HuggingFace.

### Handling the secret itself

Non-negotiable, and easy to get wrong:

- `dedupe_key = hash(detector_id, sha256(secret_value))` — **never** the value.
- The plaintext is never stored, never in the finding title, never rendered in a report,
  never logged. Display a masked prefix only.
- A finding for a *live* secret is severity 5 and should reach the user through a channel
  that does not itself publish the value.
- Report the **rotation** action, not just the detection. A secret found and not rotated is
  a secret still leaked; the finding should link the provider's revoke page directly.

---

## 6B — The AI-specific rule set (weeks 4–7)

Deterministic static analysis — tree-sitter for parsing, Semgrep-style rules for matching.
No model decides what is vulnerable; §2 rule 2 holds unchanged.

| Rule | Detection | Severity |
|---|---|---|
| **Model output → execution sink** | Dataflow from an LLM response to `eval` / `exec` / `subprocess` / `os.system` | 5 |
| **Model output → SQL or path** | Same dataflow into a query builder or filesystem path | 5 |
| **Unsanitised prompt interpolation** | User-controlled input concatenated into a prompt with no delimiter or escaping | 4 |
| **Tool handler without authorisation** | A registered tool/function performing a privileged action with no caller check | 4 |
| **Unpinned model weights** | `from_pretrained(...)` / `hf_hub_download(...)` with no `revision` or SHA | 3 |
| **MCP server exposed** | Server bound to `0.0.0.0` or listening with no auth configured | 4 |
| **RAG injection surface** | Retrieved documents concatenated into a prompt without provenance separation | 3 |
| **Unvalidated structured output** | Model JSON parsed and used without schema validation | 2 |
| **Secrets in notebook output** | (shares the 6A detector) | 5 |

Map each rule to **MITRE ATLAS** technique IDs. It is the recognised vocabulary for
AI-system threats, and citing it is the difference between a rule pack and a credible one.

**The suppression discipline from §1 applies here too.** "This `eval` is reachable only
from a test fixture" is as valuable as flagging it, and far rarer among scanners.

---

## 6C — General SCA, SBOM, and reachability (weeks 8–13)

The original [`PRODUCT_PLAN.md`](../PRODUCT_PLAN.md) §8 design, unchanged in substance.

- **SCA** against OSV.dev + GitHub Advisory Database.
- **SBOM** in CycloneDX.
- **Reachability** — for each advisory with known affected symbols, search the call graph
  for a path from any entrypoint to that symbol.

### Suppression is the headline, not the alerts

> `CVE-2026-1234` in `libfoo@1.2.3` — **not reachable**. No path from 14 entrypoints to
> `libfoo.parse_header`. Suppressed. [show analysis]

Telling a developer what they can safely ignore is rarer, more memorable, and more
trust-building than another alert. Most tools do reachability badly or paywall it.

### Be honest about the limits

Dynamic dispatch, reflection, `eval`, and DI containers defeat static reachability. Report
`unreachable (high confidence)` versus `unreachable (dynamic dispatch present — verify
manually)` and never collapse the two. **Overclaiming here is the one failure mode that
could genuinely hurt a user** — a suppressed-but-actually-reachable CVE is worse than no
tool at all.

### This un-defers the sandbox

Running Semgrep, `govulncheck`, and ecosystem resolvers against untrusted repositories
means executing hostile code paths. The Phase 0 sandbox design applies in full and is
non-negotiable: no network egress by default, read-only root FS, tmpfs workdir, CPU/mem/pid
limits, wall-clock timeout, non-root uid, seccomp profile. gVisor or Firecracker if
available; a hardened container with `--network=none` at minimum.

**Write the threat model down before writing the runner.** Together with Phase 5's vault,
this brings back both items deferred out of Phase 0 — see the roadmap note on cost.

---

## Ship criteria

- [ ] 6A: ≥95% precision on live-secret findings, measured against verified liveness
- [ ] 6A: zero plaintext secrets in the database, logs, reports, or API responses —
      verified by test, not by inspection
- [ ] 6A: notebook-output detection catches a planted secret in `.ipynb` JSON that
      source-only scanners miss
- [ ] 6B: rule pack published, versioned, ATLAS-mapped; ≥85% precision on a held-out set
      of real AI repos
- [ ] 6C: reachability reports both directions with confidence tiers; **no
      `unreachable (high confidence)` finding is ever wrong** on the eval set
- [ ] Sandbox: documented threat model; no egress, verified by an escape-attempt test

---

## Risks

**Suppression errors are the asymmetric risk.** A false positive wastes someone's hour. A
false *negative* dressed as "unreachable, no action needed" is us telling a user a real
vulnerability is safe. Tier the confidence and never let the high-confidence tier be wrong.

**AI security is a fast-moving target.** The rule set will age faster than anything else in
Cadence — MCP barely existed two years ago. Version the pack, date the rules, and treat
staleness as a defect rather than assuming a shipped rule stays correct.

**Sandbox escape.** We will be running arbitrary dependency resolution from strangers'
repositories. Treat every checkout as hostile. Per §13, an escape stops all feature work
until resolved and disclosed.

---

# Execution checklist

Moved from `ROADMAP.md` 2026-08-30.

## 6A — weeks 17–19 (secrets; uses logs already stored)

- [ ] CI-log secret scanning — the surface no scanner covers
- [ ] `.ipynb` **output** scanning (secrets live in committed notebook JSON)
- [ ] Verified liveness against provider token-info endpoints (~60% → ~99% precision)
- [ ] `dedupe_key = hash(detector_id, sha256(value))`; plaintext never stored/logged/rendered
- [ ] Finding links the provider's revoke page — rotation is the action, not detection

## 6B — weeks 31–34 (the AI rule pack)

- [ ] Model output → `eval`/`exec`/shell/SQL/path dataflow rules
- [ ] Unsanitised prompt interpolation · tool handler without authz · unpinned weights ·
      MCP exposure · RAG injection surface · unvalidated structured output
- [ ] **MITRE ATLAS** technique IDs on every rule
- [ ] Published as an open versioned ruleset (`cadence-ai-security`)

## 6C — weeks 41–46 (general SCA + reachability; un-defers the sandbox)

- [ ] OSV.dev + GitHub Advisory DB; CycloneDX SBOM
- [ ] Reachability with **suppression as the headline**
- [ ] Confidence tiers: `unreachable (high confidence)` vs `(dynamic dispatch — verify)`
- [ ] Sandbox: no egress, read-only root, tmpfs, cpu/mem/pid caps, non-root, seccomp.
      **Threat model written before the runner.**

## Ship criteria

- [ ] ≥95% precision on live-secret findings
- [ ] Zero plaintext secrets anywhere, verified by test
- [ ] Notebook detection catches a planted secret source-only scanners miss
- [ ] AI rule pack ≥85% precision on held-out real AI repos
- [ ] **No `unreachable (high confidence)` finding is ever wrong** on the eval set
- [ ] Sandbox escape-attempt test passes

## Scope warning — 6A is the only part that clearly belongs

Taken together, 6A + 6B + 6C resemble a standalone security platform, and security is a
large, well-funded, competitive category. The strategy review's objection is fair and worth
recording plainly.

**6A is different in kind from 6B and 6C.** It runs on logs Cadence *already ingests and
stores*. Nobody else has that corpus, because nobody else keeps CI logs. *"An AWS credential
appeared in 7 historical CI runs, and it is still live"* is a finding only this product can
produce — and it is the same substrate argument that justifies everything in Phase 1.

**6B and 6C are not.** They scan source and dependencies, which is Snyk's, Dependabot's and
GitHub Advanced Security's ground, and 6C drags the sandbox back in as a prerequisite.
Neither uses the CI history that makes Cadence defensible.

**Recommendation:** keep 6A on the roadmap. Move 6B and 6C behind explicit user demand — a
named user asking for them — rather than a calendar slot. If the plan slips, these are the
first things to cut, which the existing cut order (6C → 5C → 6B) already says.

## Already applied to our own repository

Not a phase deliverable, but worth recording because it rehearses 6A/6C machinery: this
repo's own CI SHA-pins every action, runs `zizmor` against its own workflows, generates a
CycloneDX SBOM on every build, and publishes an OpenSSF Scorecard. See
[`../../SECURITY.md`](../../SECURITY.md), which also lists the honest gaps — no fuzzing, a
mypy baseline, ~65% coverage.
