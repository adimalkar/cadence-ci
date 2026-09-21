-- Rate card 20260901 -- the self-hosted platform charge never happened.
--
-- Migration 004 states, as fact: "On 2026-03-01 GitHub began applying a $0.002/min
-- 'Actions cloud platform charge' to all workflow executions, self-hosted runners
-- included." That is wrong.
--
-- What actually happened: GitHub announced the charge on 2025-12-16 for self-hosted
-- runners in *private* repositories from 2026-03-01, and **postponed it indefinitely
-- within 48 hours** after community backlash. Verified 2026-09-21 against GitHub's own
-- pricing-changes page and two independent write-ups. As of today there is no self-hosted
-- platform charge and no announced timeline for reintroducing one.
--
-- What did happen, and which version 2026 already had right: hosted rates fell up to 39%
-- on 2026-01-01 (Linux x86 $0.008 -> $0.006, Windows $0.016 -> $0.010, macOS $0.080 ->
-- $0.062), with the orchestration cost folded into the reduced hosted rate.
--
-- Why this matters more than a stale comment. The sentinel is the fallback for *any*
-- runner label the card does not recognise -- `self-hosted`, `depot-ubuntu-24.04-4`,
-- `ubuntu-slim`. At $0.002/min it invented a GitHub charge that does not exist, and
-- `free_on_public = true` meant the error was invisible on the corpus (all 55 repos
-- public) while landing squarely on private repos with self-hosted runners. CAVEATS 24
-- called that population "the commercial case"; it filed the rate as correct-but-
-- unvalidated. Validated now, and wrong: we were overstating their dollar figures.
--
-- Zero is the honest number for *GitHub's* charge. It is not what the runner costs the
-- user -- EC2, hardware and power are real -- but that is the user's figure to supply,
-- not ours to invent. See `cost.py` for how an operator-supplied rate overrides this.

INSERT INTO rate_card (version, runner_label, os, cores, usd_per_minute, free_on_public, effective_from)
SELECT 20260901, runner_label, os, cores, usd_per_minute, free_on_public, DATE '2026-09-01'
FROM rate_card
WHERE version = 20260301 AND runner_label <> '__self_hosted__';

-- The sentinel, corrected. free_on_public stays true and is now redundant rather than
-- load-bearing: at $0.000 the public/private distinction changes nothing, which is
-- exactly the point.
INSERT INTO rate_card (version, runner_label, os, cores, usd_per_minute, free_on_public, effective_from)
VALUES (20260901, '__self_hosted__', 'any', NULL, 0.000, true, '2026-09-01');
