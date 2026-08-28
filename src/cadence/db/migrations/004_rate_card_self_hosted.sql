-- Rate card 20260301 -- GitHub's Actions platform charge reaches self-hosted runners.
--
-- On 2026-03-01 GitHub began applying a $0.002/min "Actions cloud platform charge" to all
-- workflow executions, self-hosted runners included. Hosted runners are unaffected in
-- price: their meters already fell up to 39% on 2026-01-01 with the platform charge folded
-- into the reduced rate, which version 2026 already reflects. Public repositories remain
-- free on both hosted and self-hosted runners.
--
-- Versioning note: version 2026 was named for its effective year, which stopped working the
-- moment a rate changed mid-year. From here versions are YYYYMMDD of `effective_from`.
-- 20260301 > 2026 so ordering still holds, and every finding stamps the version that
-- produced its figure, so older claims stay interpretable rather than silently re-priced.

-- Hosted rates are unchanged; copy them rather than re-typing ten rows and risking a typo
-- in a number that appears in a customer-facing dollar figure.
INSERT INTO rate_card (version, runner_label, os, cores, usd_per_minute, free_on_public, effective_from)
SELECT 20260301, runner_label, os, cores, usd_per_minute, free_on_public, DATE '2026-03-01'
FROM rate_card
WHERE version = 2026;

-- Self-hosted and third-party runner pools cannot be enumerated: the label is whatever the
-- repo chose -- `self-hosted`, `depot-ubuntu-24.04-4`, `codspeed-macro`, `ubuntu-slim`.
-- This sentinel is the rate for any label the card does not know, resolved by
-- RateCard.usd_per_minute after an exact-label miss. Keeping it as a row rather than a
-- constant means it is versioned and auditable like every other rate.
INSERT INTO rate_card (version, runner_label, os, cores, usd_per_minute, free_on_public, effective_from)
VALUES (20260301, '__self_hosted__', 'any', NULL, 0.002, true, '2026-03-01');
