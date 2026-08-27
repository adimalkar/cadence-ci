# Security Policy

Cadence reads CI history through the GitHub API. Today that means it holds a GitHub token
and a webhook signing secret. The roadmap adds more: Phase 2 requests write scope to open
pull requests, and Phase 5C stores customer-supplied model API keys under envelope
encryption. The care taken here is meant to be proportionate to where the project is
going, not only to where it is.

## Status

**Pre-release. No published package, no hosted service, no users to notify.** There is no
supported version table yet because there are no releases. If you are running this, you
are running it from source and you should read the code.

## Reporting a vulnerability

Please report privately, not in a public issue:

- **Preferred:** [GitHub Security Advisories](https://github.com/adimalkar/cadence-ci/security/advisories/new)
  — private by default, and lets us collaborate on a fix before disclosure.
- If that is unavailable to you, open a public issue containing **no details** and asking
  for a private channel.

Useful in a report: what you did, what happened, what you expected, and the smallest
reproduction you have. A proof of concept is welcome but never required — a clear
description of the flaw is enough.

### What to expect

This is a single-maintainer project worked on part time, so honest numbers rather than
enterprise ones: acknowledgement within **7 days**, an initial assessment within **14**.
If a report is valid and I cannot fix it quickly, you will be told that rather than left
waiting. Credit in the advisory unless you would rather stay anonymous.

## Scope

**In scope**

- Authentication and integrity of the webhook receiver — HMAC-SHA256 verification,
  replay and idempotency handling
- Leakage of tokens, webhook secrets, or (once Phase 5C lands) stored customer keys into
  logs, API responses, error payloads, or generated reports
- SQL injection, SSRF, or command injection through ingested data. Note that **run and
  job metadata, workflow YAML, and CI logs are all attacker-influenced** — anyone who can
  open a pull request against an ingested repository controls some of what we parse.
- Sandbox escape, once the Phase 6C analysis sandbox exists
- Prompt injection that causes action rather than only bad output, once Phase 5C's
  review module exists. The design rule is that model output can trigger no action.

**Out of scope**

- Vulnerabilities in GitHub Actions itself, or in the repositories Cadence analyses. If
  you find something in a third party's workflow using Cadence, report it to them.
- Findings that require an attacker to already control the machine running Cadence, or to
  already hold the database credentials
- Missing hardening with no demonstrated impact, and automated scanner output pasted
  without a working reproduction
- Denial of service through obviously unreasonable input volume against a local CLI

## What we do already

Verifiable in [`.github/workflows/`](.github/workflows/) rather than asserted here:

- Every GitHub Action is **pinned to a commit SHA**, never a tag. Tags are mutable, and
  the tj-actions compromise moved one to malicious code.
- `zizmor` audits our own workflows on every run, at `--min-severity low`
- `pip-audit` and Dependabot cover dependency advisories; `dependency-review` blocks pull
  requests that introduce high-severity ones
- CodeQL runs `security-extended` on every push and weekly
- OpenSSF Scorecard results are **published**, whether or not they flatter the project
- Workflow tokens are read-only by default, and `persist-credentials: false` keeps the
  checkout credential out of `.git/config`
- The release pipeline uses PyPI Trusted Publishing — **no long-lived API token exists in
  this repository to steal** — and attaches build provenance
- The webhook receiver has **no dev-mode bypass for signature checking**, deliberately:
  an endpoint that accepts unsigned payloads accepts payloads from anyone

## Known gaps

Stated plainly, because a security policy that lists only strengths is marketing:

- **No fuzzing.** The workflow YAML parser and the log normalizer both consume untrusted
  third-party input and are the obvious first targets when it is added.
- **Eight modules are exempt from type checking** while a pre-existing mypy baseline is
  burned down; see `pyproject.toml`.
- **Test coverage is ~65%**, and `worker.py` is materially below that.
- The Phase 5C key vault and the Phase 6C sandbox are **designed but not built**. Nothing
  in this repository should be trusted with a customer's credentials today.
