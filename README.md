# Cadence

**Evidence-grounded CI intelligence.** Cadence reads your build history, tells you what it
costs you in minutes and dollars, and opens the pull request that fixes it.

> **Status:** Phase 0 (ingest) shipped and running continuously. Phase 1 (waste audit) is
> ~84% — eight detectors, the simulator and the report work end to end against a 51-repo
> public corpus; one ship criterion is still failing and is being worked. Everything after
> that is design documents. [`docs/phases/PROGRESS.md`](docs/phases/PROGRESS.md) is measured
> against the code rather than remembered.
>
> **Apache-2.0, and contributions are welcome** — start with
> [`CONTRIBUTING.md`](CONTRIBUTING.md), or say hello in
> [Discussions](https://github.com/adimalkar/cadence-ci/discussions).

---

## The idea

Point Cadence at a repo. It reads recent workflow runs through the plain GitHub API — no
install, no agent, no config change, no build migration — and comes back with a ranked,
quantified list of what your CI wastes:

```
Your PR feedback loop is 22 minutes. The critical path is 8.
Here is the other 14, ranked by hours recovered:

1. `npm ci` runs cold every job — no cache configured    4.2 min/run · $310/mo
2. No `concurrency: cancel-in-progress` — 340 superseded
   runs finished anyway                                            $180/mo
3. `build` and `lint` serialized via `needs:` but share
   no artifacts                                          3.0 min/run

Apply 1-3: p50 feedback 22 min → 9 min. ~$490/mo recovered.
[Open fix PR for #1]  [Show the analysis]
```

Every number comes from replaying real historical runs, not from asking a model. Every
claim links to the runs it came from.

## Why it's different

The field splits into two camps that never meet. **Config readers** (actionlint, zizmor,
poutine) parse your workflow YAML — but only for correctness and security; none of them
know how long anything took. **History readers** (CI cost dashboards, Datadog) measure
where your minutes went — but never open your workflow file to say which line caused it.

*"Your `needs:` edge on line 34 is false, and removing it saves 3.0 min/run across your
last 1,400 runs"* requires both halves.

## The four rules

1. **Evidence or it doesn't ship.** Every finding cites specific runs, a config range, or a
   timing series — enforced by a database constraint, not by code review.
2. **The LLM is never the detector.** Detectors produce findings and evidence; the model
   only writes prose. If the API call fails, the finding still ships.
3. **Read-only until explicitly invited.** Diagnosis needs no write permission at all.
4. **Never a gate.** Check runs conclude `neutral`, never `failure`.

## What this is not

Not a code review bot — we never comment on your diff. Not a CI runner, not a build system,
not a merge queue. The tool we sit next to is GitHub Actions itself; our output targets
your `.github/workflows/`, not your source.

---

## Development

Requires Python 3.12+, PostgreSQL 14+, and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --extra dev
cp .env.example .env          # CADENCE_GITHUB_TOKEN=$(gh auth token)

createdb cadence
uv run cadence db init        # apply schema

uv run cadence ingest astral-sh/ruff --limit 100
uv run cadence stats astral-sh/ruff
```

Tests:

```bash
uv run pytest
```

## Docs

| | |
|---|---|
| [`docs/PRODUCT.md`](docs/PRODUCT.md) | What we build and why — canonical |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | 24-week execution checklist |
| [`docs/phases/`](docs/phases/) | Design docs, one per phase |
| [`docs/EXPANSION.md`](docs/EXPANSION.md) | Researched feature candidates, ranked |
| [`docs/PRODUCT_PLAN.md`](docs/PRODUCT_PLAN.md) | Engineering appendix |
| [`docs/phases/PROGRESS.md`](docs/phases/PROGRESS.md) | How far into each phase, measured against the code |
| [`docs/CAVEATS.md`](docs/CAVEATS.md) | Standing ledger of known gaps, bugs and deliberate compromises |

## Contributing

Apache-2.0. [`CONTRIBUTING.md`](CONTRIBUTING.md) covers setup and the four rules a change
has to respect — the unusual ones are that **evidence is enforced by a database trigger**,
and that **the LLM is never the detector**.

Issues labelled [`good first issue`](https://github.com/adimalkar/cadence-ci/labels/good%20first%20issue)
are scoped to be completable without knowing the whole system. Vulnerabilities go through
[`SECURITY.md`](SECURITY.md), never a public issue.

## License

Apache-2.0.
