## What this changes, and why

<!-- What the obvious alternative was, and why it was wrong, is usually the useful half. -->

## What you measured

<!--
Only if this touches a detector, the cost model, or the simulator. Numbers beat adjectives:
how many repos did it fire on, out of how many, and what did it find before?
Delete this section if it does not apply.
-->

## Checklist

- [ ] CI is green (`ci-gate` aggregates every job)
- [ ] Tests cover the change, and none of them are skipped — a skipped test fails the build
- [ ] Any new finding carries evidence; nothing writes to `finding` outside `findings.py`
- [ ] Replay and projection stay distinct — no projection rendered as a point value
- [ ] Docs updated if behaviour changed, and `docs/CAVEATS.md` appended if this left
      anything unfinished, uncertain, or knowingly compromised
