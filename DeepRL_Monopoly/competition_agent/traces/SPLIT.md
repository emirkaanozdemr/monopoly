# EXPLORE / CONFIRM split — recorded before any trace analysis

Declared: 2026-08-13, before the full traced pass finished and before any
per-seed statistic other than win/loss (already public in
probes/field_strong_rank.partial.jsonl) was computed.

- Seed set: 960000..961999 (the Candidate D strong-field run, seat = seed % 4).
- **EXPLORE  = seeds 960000..960999** (n=1000)
- **CONFIRM  = seeds 961000..961999** (n=1000)

Both halves contain each seat 250 times (seed % 4 is uniform on contiguous
blocks of 1000). CONFIRM traces are not to be read, aggregated, or plotted
until the Phase 4 hypothesis list is frozen in GAPS.md.

Phases 2 and 3 (invariant checks, coverage) are outcome-independent and run
over ALL 2000 games by design; they are not gated by this split.
