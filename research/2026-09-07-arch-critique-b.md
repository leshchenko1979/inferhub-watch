# Architecture Critique B — inferhub-watch (subagent watch-arch-review, 2026-09-07)

NOTE: this report arrived truncated via the harness; the tail below (from C-section onward) was restored from the delivered output. Load-bearing claims were independently re-verified where stated.

## Critical (C)

- **C1 — pages deploys stale data with exit 0.** pages job runs unconditionally with `if: always()`-style behavior; probe failure or empty aggregates still publish yesterday's data, indistinguishable from fresh. No freshness gate.
- **C2 — pricing.main exits 0 on empty aggregate** — a zero-row fetch silently writes an empty/absent block.

## Major (M)

- **M1 — no owner alerting on staleness** — stale-record goes to stderr + a CI annotation nobody reads.
- **M2 — render tests assert exact substrings/order against live data/*.json** — the suite is hostage to daily data drift; every data shape change breaks ~40 substring tests.
- **M3 — check accessors hard-coded in generate.py** — the "add a check" plugin promise stops at the runner; the renderer must be edited too.
- **M4 — no committed fixture corpus** — tests read live data files (same root as M2).

## Nits (N)

- **N1 — non-atomic JSON writes** — write_outputs/record_proven share the non-atomic write pattern; a shared atomic_write_json would standardize.
- **N2 — ONTOLOGY.md stale on schedule** — says "Sweep 06:00Z" while watch.yml runs 02:00Z (deliberately changed for drift). The repo enforces terminology in tests, so the one factual claim in it must be fixed.
- **N3 — probe job unbounded; schedule/dispatch can overlap** — concurrency keyed on event name means manual dispatch during the scheduled run probes InferHub twice (billed) and races commits; rebase-retry self-heals push conflicts but a genuine pricing.json content conflict can lose today's run, then pages silently deploys old data (C1). No timeout-minutes on probe (default 360). Fix: single concurrency group + timeout-minutes: 60.
- **N4 — orphaned/test-only functions in site/** — _iq_cells (generate.py:412), peer_bounds (rundata.py:401), display_specs (rundata.py:53) unreachable in production, kept alive by tests; _iq_cells actively misleads.
- **N5 — run.py exits 0 even when every cell failed** — "probed and everything failed" indistinguishable from "didn't probe" in downstream gates; consider exit/annotation when runner_errors non-empty or all cells error.

## Nits (raw)

- results_available() semantics misnamed; called twice, re-loads all runs both times.
- shell() re-reads board.js from disk every render — import once.
- Two money formatters, parse_ts divergence (cross-confirms critique A's W4).

## Top 3 refactor priorities

1. **Failure transparency (C1+M1)** — freshness gate in pages job; non-zero + loud tripwires for empty aggregates in pricing.main; staleness chip in the header. Smallest change, largest trust payoff.
2. **Split site/generate.py (M2+M3)** — section modules under site/sections/, copy into templates, delete dead sort key and _iq_cells, drive check accessors from the registry. Do AFTER #3.
3. **Freeze the render-test corpus (M4)** — committed fixture set; structural assertions replace exact-substring ones. Enabler for #2.

**Keep intact:** pure-function check tests + canonical-payload fixtures; ONTOLOGY discipline (fix N2); single-copy invariants (market.family, cache_rule_stats, rate_label delegation); degrade-gracefully-but-record-why (but route the record somewhere a human sees).
