# Held-Out Repositories

**Chosen 2026-08-06, week 1, before any detector existed.**

> **Do not look at these repos' data during development.** Not to debug a rule, not to
> check a hunch, not "just once." Every glance converts a held-out repo into a training
> repo and inflates every number we later publish.

## Why repo-level, not sample-level

Sampling findings across all repos and holding out a fraction leaks badly: repos have
house styles, a shared CI template, the same maintainer's habits. A rule tuned on 80% of a
repo's findings will look excellent on the remaining 20% and mediocre on a repo it has
never seen. Repo-level splits are the only honest measure of "works on a codebase we did
not develop against."

## Selection criteria

Picked for spread across the axes that plausibly change rule behaviour, not for
convenience:

- **Ecosystem** — the cache, dependency, and test-framework rules are all ecosystem-specific
- **CI shape** — wide matrix vs. narrow; monorepo vs. single package
- **Scale** — run volume changes what statistical rules can conclude
- **Runner mix** — provisioning overhead varies enormously (measured: 1.7% on prettier,
  9.5% on ruff)

## The set

| Repo | Ecosystem | Why it's here |
|---|---|---|
| `home-assistant/core` | Python | Enormous matrix, very high run volume |
| `pandas-dev/pandas` | Python / C ext | Long builds, compiled extensions, heavy caching |
| `vercel/next.js` | JS monorepo | Turborepo, complex job graph |
| `withastro/astro` | JS monorepo | pnpm, changesets, moderate scale |
| `tokio-rs/tokio` | Rust | cargo caching, cross-platform matrix |
| `bevyengine/bevy` | Rust | Very long compiles, large artifacts |
| `grafana/grafana` | Go + JS | Mixed-ecosystem, two toolchains in one pipeline |
| `kubernetes/minikube` | Go | Heavy integration suites, known flake |
| `spring-projects/spring-boot` | Java / Maven | JUnit, the ecosystem the flaky-build research covers |
| `elastic/logstash` | Java / Ruby | JRuby, unusual toolchain, awkward edge cases |
| `rails/rails` | Ruby | Large test suite, service containers (DB, Redis) |
| `symfony/symfony` | PHP | Wide version matrix, underrepresented ecosystem |
| `dotnet/runtime` | C# | Enormous scale, self-hosted mix |
| `sveltejs/svelte` | JS | Mid-size, Playwright browser tests |
| `apache/airflow` | Python | Massive matrix, Docker-heavy, notoriously slow CI |

15 repos. All public, all with visibly busy CI.

## Development corpus (safe to look at)

Rules are built and tuned against these. Currently ingesting:

- `astral-sh/ruff` — 150 runs, 2,046 jobs, 15,702 steps
- `prettier/prettier` — 100 runs, 307 jobs, 3,334 steps

The corpus expands toward ~50 repos during Phase 0. None of the held-out 15 may join it.

## Protocol

1. Held-out repos are ingested continuously from week 1 — history must accumulate, since
   it cannot be backfilled past GitHub's 90-day log retention.
2. Their data is queried **only** by the eval harness, never interactively.
3. Every published precision or calibration number states which of these repos it covers,
   with n and the detector version SHA.
4. If a held-out repo is ever used for debugging, it is retired from the set permanently
   and recorded here. Replacing it silently is the same as lying about the number.

## Retirements

*(none yet)*
