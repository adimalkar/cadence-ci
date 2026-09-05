# Contributing to Cadence

Cadence is Apache-2.0 and open to contributions. This file is short on ceremony and long on
the two or three things that are genuinely unusual here, because those are what a pull
request gets sent back for.

**New here?** Issues labelled [`good first issue`](https://github.com/adimalkar/cadence-ci/labels/good%20first%20issue)
are scoped to be completable without knowing the whole system. Questions that are not bug
reports belong in [Discussions](https://github.com/adimalkar/cadence-ci/discussions).

---

## Getting it running

Requires Python 3.12+, PostgreSQL 14+, and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --extra dev
cp .env.example .env              # CADENCE_GITHUB_TOKEN=$(gh auth token)

createdb cadence
uv run cadence db init            # apply schema

uv run cadence ingest astral-sh/ruff --limit 100
uv run cadence audit astral-sh/ruff
uv run pytest
```

A GitHub token with public-repo read is enough for everything except opening pull requests.
Ingest is read-only by design — see rule 3 below.

---

## The four rules

These come from [`docs/PRODUCT.md`](docs/PRODUCT.md) and they are not stylistic. A change
that breaks one of them will not be merged regardless of how well it works.

### 1. Evidence or it doesn't ship

Every finding cites the specific runs, config range, or timing series it came from. This is
enforced by a **database trigger** (`finding_requires_evidence`), not by review — a finding
inserted without evidence is rejected by Postgres.

If you add a detector, it returns drafts with evidence attached and lets
[`findings.py`](src/cadence/findings.py) persist them. Do not write to `finding` directly.

### 2. The LLM is never the detector

Detectors are deterministic code. Models write prose, and only prose. If the API call fails,
the finding still ships with its title, its numbers, and its evidence intact.

There is no exception to this. A rule that needs a model to decide whether something is true
is a rule we do not ship.

### 3. Read-only until explicitly invited

Diagnosis needs no write permission. Ingest, audit, and every detector run against read
scopes only. Write access is opt-in, separately, at install time, and exists only for
Phase 2's fix PRs.

### 4. Never a gate

Check runs conclude `neutral`, never `failure`. Cadence tells you what your CI costs; it
does not block your merge.

---

## Replay and projection must not be blended

The credibility model in `PRODUCT.md` §6 is the thing most likely to trip up an otherwise
good PR.

| Basis | What it means | How it renders |
|---|---|---|
| **replay** | Arithmetic over timings that actually happened | Solid, a point value |
| **projection** | A prediction about something that has not run | Hatched, **a range** |

A projection rendered as a point value is a bug, even when the number is right. Never
average the two into one headline — the blend destroys what makes either believable.

---

## Working on a detector

Detectors live in [`src/cadence/detectors/`](src/cadence/detectors/) and are the most
approachable place to start. Each one:

- takes a `Context` of observed runs, jobs, steps and parsed workflow config
- returns finding **drafts**, never database rows
- attaches evidence to every draft
- **stays silent when it cannot be sure** — a rule with nothing solid to say emits nothing

That last point is the house style. Cadence competes on precision, not coverage. Withholding
a finding costs one missed insight; a wrong finding costs the user's trust in every other
number on the page, and that does not come back.

Two conventions worth copying from existing detectors:

- **Guards are constants at module scope** with a comment saying why that value
  (`MIN_RUNS`, `MIN_WASTE_FRACTION`), so the threshold can be argued with.
- **Name what you excluded.** If you drop implausible data, put the count in the evidence
  payload. `cancellation.py` reports `runs_excluded_implausible_span` for exactly this
  reason, and it is how a bad number gets caught.

---

## Pull requests

- **Branch and open a PR.** Never push to `main`.
- **CI must be green.** `ci-gate` aggregates every job and is the one required check —
  lint, typecheck, security (pip-audit + zizmor), migrations, tests on 3.12 and 3.14, and
  the build. Coverage has a floor and **skipped tests fail the build**, so a test that needs
  Postgres must actually run.
- **One logical change per PR.** Splitting is nearly always right.
- **Say what you measured.** A PR that claims a detector finds more waste should say on how
  many repos, out of how many, against what it found before.

Commit messages are prose, not conventions theatre: say what changed and, more importantly,
why the obvious alternative was wrong.

### Actions are pinned to SHAs

Every action in `.github/workflows/` is pinned to a commit SHA with a trailing version
comment. Tags are mutable — the tj-actions compromise moved a tag to malicious code and
every workflow tracking that tag executed it. Dependabot bumps the SHAs. Do not replace one
with a tag.

---

## Where the project is

[`docs/phases/PROGRESS.md`](docs/phases/PROGRESS.md) is measured against the code rather than
remembered, so it is the honest answer to "what is actually built." Phase 0 is shipped and
Phase 1 is close; everything after that is design documents.

[`docs/CAVEATS.md`](docs/CAVEATS.md) is the standing ledger of known gaps, bugs found in
passing, and deliberate compromises. **Read it before assuming something is an oversight** —
it is very likely already written down, along with what would close it. Several entries are
corrections of earlier claims that turned out to be wrong, which is the intended use.

---

## Reporting a vulnerability

Do not open a public issue. See [`SECURITY.md`](SECURITY.md).

## Code of conduct

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).
