# Phase 0 — Ingest Platform

**Weeks 1–3 · Prerequisite for everything · No user-facing surface**

---

## What this phase delivers

A database that knows everything GitHub will tell us about a repo's CI history, kept
current, with no install required to start.

Nothing in Phase 0 is visible to a user. Its only job is to make Phases 1–4 possible, and
to start the clock on the one asset that cannot be acquired retroactively: run history.

## Why it comes first and why it is short

The earlier plan budgeted 5 weeks here because it included a hardened sandbox for
executing untrusted code and a KMS-backed secret vault. **Neither is needed anymore.** The
waste audit reads metadata and logs; it executes nothing. Both items move out of the
critical path entirely and only return if we build the deferred static-analysis line.

That is the single largest schedule saving in the replan: 5 weeks → 3.

---

## Scope

### In

- Read-only ingest of public repos via GitHub REST + a personal token. No App install.
- GitHub App (registered, dev mode) with webhook receiver for repos that do install.
- Postgres schema: `repo`, `commit`, `run`, `job`, `step`, `log_chunk`, `finding`,
  `evidence`.
- Object storage for compressed logs.
- Job queue.
- `CIProvider` interface with exactly one implementation.

### Out

- Sandbox / untrusted code execution → deferred with static analysis
- Secret vault → deferred; not needed until BYO-key
- Any detector, any finding, any UI

---

## The data we actually need

Everything below is available read-only, and per-step timing — the input the whole
counterfactual simulator runs on — comes free in the jobs endpoint.

| Endpoint | Gives us | Feeds |
|---|---|---|
| `GET /repos/{o}/{r}/actions/runs` | run status, conclusion, event, `run_attempt`, timestamps, head SHA | everything |
| `GET /repos/{o}/{r}/actions/runs/{id}/jobs` | **per-step `started_at`/`completed_at`**, runner labels, job `conclusion` | simulator, critical path |
| `GET /repos/{o}/{r}/actions/runs/{id}/logs` | zipped per-job logs | cache detection, failure signatures |
| `GET /repos/{o}/{r}/actions/cache/usage` | cache size + entry count | cache rules |
| `GET /repos/{o}/{r}/contents/.github/workflows` | workflow YAML | config rules, fix PRs |
| `GET /repos/{o}/{r}/commits/{sha}/check-runs` | other bots' app slugs | bot deference |
| `GET /repos/{o}/{r}/deployments` + `deployment_status` | deploy events | Phase 4 DORA |

**`run_attempt` is load-bearing.** It is how retries are identified, and retry behaviour is
the strongest flaky signal we get. Capture it from day one — it cannot be backfilled once
the retention window passes.

### Retention is a hard deadline

GitHub retains logs for **90 days by default** (configurable 1–400 days for private repos,
1–90 for public). Anything not ingested inside that window is gone permanently. This is
the concrete reason ingest starts in week 1 rather than after the detectors are written.

---

## Prior art

### Apache DevLake — the closest thing that exists

[apache/devlake](https://github.com/apache/devlake) is a mature Apache-governed dev data
platform. It ingests GitHub, GitLab, Jenkins, BitBucket, Azure Pipelines, Jira, and
SonarQube, and ships prebuilt DORA dashboards. It genuinely overlaps our ingest layer and
most of Phase 4.

What it does **not** do: cost analysis, waste detection, workflow optimization
recommendations, or flaky test detection. It is an ingest-and-visualize platform — the
analysis layer is left to you writing SQL against its warehouse.

**What we take from it:** its data model is a good sanity check on ours, and its plugin
architecture is a well-tested version of our `CIProvider` interface.

**What it means strategically:** do not compete on ingest breadth. DevLake already won
that, it has Apache governance, and matching it is a year of work with no differentiation
at the end. Our ingest is deliberately narrow — GitHub Actions only, but at *step*
granularity, which DevLake does not retain because dashboards don't need it. Step timings
are exactly what the simulator needs. Narrow and deeper, not wide.

**Worth evaluating later:** a DevLake-as-ingest-source adapter would give us Jenkins and
GitLab for free without writing four providers. Revisit at week 20, not now.

### Other tooling

- **[DevoteamNL/opendora](https://github.com/DevoteamNL/opendora)**, Google's Four Keys —
  DORA-specific, no step-level retention.
- **Prometheus GHA exporters** — emit aggregate gauges, discard the per-run rows we need.
  Wrong shape entirely.

### Verdict

There is no OSS project that stores GitHub Actions history at step granularity for
analysis. This layer has to be built. It is also the least interesting part of the product
— budget it tightly and do not gold-plate it.

---

## Design decisions

**Postgres for the job queue.** `SELECT … FOR UPDATE SKIP LOCKED`. No Redis, no Celery,
until there is a measured reason. One less thing to operate.

**Object storage: R2 or B2**, not S3 — meaningfully cheaper egress for this workload. Gzip
everything, content-addressed by sha256 so a job's log is ever downloaded once
(`LocalLogStore`, week 1 — local filesystem now, same key shape as an S3 object so the
backend swap is later a config change, not a schema change).

Measured on 25 real jobs from `astral-sh/ruff`: **5.2:1**, not the ~20:1 originally
assumed — CI logs vary a lot in verbosity, and this sample is small. Re-measure once the
corpus has meaningfully more logs stored and revise the storage-cost line in
`PRODUCT.md` §12 if the gap holds.

**Webhook receiver discipline.** HMAC-SHA256 signature verification, `X-GitHub-Delivery`
stored for idempotency, **respond 200 in <500ms and process async.** GitHub disables
endpoints that respond slowly.

Events to subscribe: `workflow_run`, `workflow_job`, `check_suite`, `pull_request`,
`push`, `deployment_status`.

**Minimum App permissions**, and no more — every extra permission costs installs:
`checks:read`, `actions:read`, `contents:read`, `metadata:read`. Note that
`pull_requests:write` is **not** in Phase 0; it is requested only in Phase 2 when the user
opts into fix PRs.

**`CIProvider` interface** — `fetch_runs`, `fetch_logs`, `normalize_event`, `post_result`.
GitHub Actions is the only implementation for the first 24 weeks. One hour of design
discipline in week 1 against a month of refactoring in week 30.

---

## Rate limits

5,000 req/hr per installation; 60/hr unauthenticated. Log download is the hog — each is a
redirect to a signed blob URL and the zip can be hundreds of MB.

Mitigations, in order of importance:

1. **Never re-download a log.** Content-address by `run_id`; store the gzip; the API is
   consulted once per run, ever.
2. **Conditional requests.** `If-None-Match` with stored ETags; 304s don't count against
   the limit.
3. **Back off on 403 with `Retry-After`**, and treat secondary rate limits as a first-class
   state in the queue rather than a retry loop.
4. **Prioritize the queue** — recent runs before backfill; a repo being actively audited
   before the passive corpus.

For the 50-repo no-install corpus, budget roughly one full-history backfill per repo
(hundreds of runs, hundreds of MB) and then a delta poll every 30 minutes.

---

## Schema notes

The `finding` / `evidence` tables from `PRODUCT_PLAN.md` §4 land here, including the
`CHECK` constraint and the insert trigger enforcing "no finding without evidence" — plus
the savings columns from `PRODUCT.md` §8. Build the constraint now even though no detector
exists yet; retrofitting a structural rule after four modules depend on it is how the rule
quietly dies.

Two additions specific to this phase:

```sql
-- step timings are the simulator's entire input; index for window queries
CREATE INDEX step_job_started_idx ON step (job_id, started_at);
CREATE INDEX run_repo_created_idx ON run (repo_id, created_at DESC);

-- idempotency for webhook replay
CREATE TABLE webhook_delivery (
  delivery_id  uuid PRIMARY KEY,   -- X-GitHub-Delivery
  event        text NOT NULL,
  received_at  timestamptz NOT NULL DEFAULT now()
);
```

---

## Ship criteria

1. 1,000 consecutive webhook deliveries produce correct rows with **zero drops and zero
   duplicates** — verified by replaying deliveries from GitHub's UI and diffing DB state.
2. Full history backfill of a repo with ≥500 runs completes without tripping a rate limit.
3. Step-level timings reconstruct total run duration to within 2% for 50 sampled runs.
4. 50 public repos ingesting continuously on a 30-minute delta poll.

Criterion 3 matters more than it looks: if step timings don't sum to wall-clock, the
simulator is built on sand and every dollar figure downstream is wrong.

---

## Risks

**Retention window.** Every day of delay permanently costs a day of history across the
whole corpus. This is the only truly irreversible risk in the entire plan.

**Log volume.** 200GB compressed is the planning assumption. If the 50-repo corpus lands
much heavier, sample logs rather than expanding storage — full logs are only needed for
runs that produce findings.

**Scope creep into DevLake territory.** The temptation to "just add GitLab" will appear
around week 2. The interface exists so that this can be said no to cheaply.
