# inferhub-watch — Consolidated Roadmap (2026-09-07)

Sources: arch-critique A (`-critique-a.md`), arch-critique B (`-critique-b.md`),
owner decisions in session 2026-09-07. Supersedes the "recommended order" in both
critique files and the shelf presented 10:49Z.

## Decision log (owner, 2026-09-07)

| Decision | Call |
|---|---|
| Postgres cache for usage logs | **YES** — supersedes the W1 band-aid; fix the cause, not the flag |
| Replace Python generator with common SSG | **NO** — bespoke dashboard; migration = 2nd language + rewrite of 426 render tests, zero user gain |
| Jinja2 templating (generator side) | **YES, direction approved** — pairs with generate.py split |
| Client-side rehydration | **NO** — kills no-JS render, breaks render-pinning tests, solves a problem we don't have |
| Shelf 1 (tps guard → probe cells), 2 (probes-only flag), 3 (cron 02:00Z) | **DONE** (`a729a5a`, `fd6c7d1`) |
| Shelf 4 (hit-rate tag), 5 (client-side sort/filter JS) | **DECLINED** |
| Shelf 6 (guard branch) | re-check: finish_reason fix landed in OC; verify whether probe tests/data contradict real traffic (IN PROGRESS) |
| Shelf 7 (family profile default) | owner: ops default = cbcn/glm-5.3-flash (verified) |
| Render fixes (4-dec prices, dot labels, binary in-use) | **DONE** (`8e4d7dd`) |
| Cache-hygiene (poisoned EWMA) | **DONE** (`ae7f9ef`) |
| QA findings (anchors, tooltips, label collisions) | **DONE** (`b023c5a`) |

## Work items — merged, deduplicated, ordered

### P0 — Data truth (fix the W1 cause properly)

1. **Postgres usage-log cache** (owner-approved; supersedes critique rec 1 and W1 flag)
   - Schema `inferhub_logs` on apps Postgres; nightly sweep upserts raw usage rows keyed by request id (idempotent).
   - `pricing.py` aggregates from SQL — real 30d window, no 12,000-row cap, no API pagination in the hot path.
   - Probe-sweep rows excluded at query time → kills marginal-price fragility at the root.
   - Size: ~one session. Owns: this session.
2. **Failure transparency** (critique B C1+C2+M1, unchanged by 1)
   - Freshness gate in pages job; non-zero exit + loud tripwire on empty aggregate; staleness chip in page header.
   - Size: small.

### P1 — Decision integrity (W2 + N3/N5)

3. **One money-basis core** (`probe/basis.py`)
   - Single owner of per-route realized / marginal / projection + authoritative basis gate; board, ticket, scatter, incumbent_bar, radar all consume it.
   - While in there: delete dead `_iq_sort_key` (generate.py:652), collapse W4 duplicates (candidate_cache_pct, parse_ts, load_pricing, newest-run, two money formatters → one each).
   - Size: medium.
4. **CI guardrails** (N3+N5)
   - Single concurrency group across schedule/dispatch; `timeout-minutes: 60` on probe; run.py non-zero exit when all cells fail.
   - Size: small.

### P2 — Structure (do after P1; fixture corpus is the enabler)

5. **Freeze the render-test corpus** (M2+M4)
   - Committed fixture data/*.json; structural assertions replace exact-substring ones. Must precede the template migration so the output contract is pinned.
6. **Split generate.py + Jinja2 templates** (W3 + M3 + owner-approved templating)
   - Section modules under `site/sections/` with plain-data context; Jinja2 templates replace f-string HTML; check accessors driven from the registry; delete orphaned test-only fns (`_iq_cells`, `peer_bounds`, `display_specs`).
   - Size: 1–2 sessions.
7. **Policy to config + shims to graves** (W5+W7)
   - FAMILY_ALIASES / DATED_FAMILIES / screen thresholds → models.toml; remove the 09-02 "failures or errors" legacy fallbacks + LEGACY_WINDOW once schema is versioned.
   - Size: small.

### Backlog (low)

8. **Hygiene nits** — atomic_write_json shared helper (N1); ONTOLOGY.md schedule line fix (N2, must-do with any CI edit); results_available() re-load; shell() re-reading board.js per render (N4/nits).
9. **Snapshot slimming** (W6) — marginal timestamp caps → aggregate only; prunes pricing.json bloat.
10. **Guard branch adoption** (shelf 6) — verify finish_reason fix vs probe tests + cache-test-vs-real-traffic contradiction check (in progress); adoption by rostered editor remains HQ-side.

## Explicitly rejected (do not re-propose without new facts)

- Postgres for *page state* / client rehydration / SPA architecture (owner: no).
- Common SSG migration (owner: no).
- Client-side sort/filter JS on the merged table (owner: no — zero-dep page stands).
- Self-hosted runner for schedule drift (advised against; cron shifted to 02:00Z instead).
