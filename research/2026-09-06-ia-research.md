# Watch page IA research — tables, sorting, navigability
2026-09-06 · research only, no code changed

## 1. What the page actually is today (verified from `site/dist/index.html`)

13 `<table>` elements on one page, 105 KB HTML, 29 `<details>` blocks.

| # | Table | Columns | Where it lives |
|---|---|---|---|
| 1 | Verdict ticket mini-table | Route, eff $/M, IQ per $ | top hero (`#verdict`) |
| 2 | Cost per M (main) | route, rates, official, deltas | `#pricing` |
| 3 | Official-price comparison | 4 cols | collapsed `#evidence-official` |
| 4 | Failures | route, attempts, failed, fail %, codes | collapsed `#evidence-failures` |
| 5 | Main-traffic speed | route, requests, ttft p50, tps | collapsed `#evidence-perf` |
| 6–13 | **Per-family probe tables ×8** | **identical 7 cols**: tests, cache, ttft, tps, ask in/out, window | `#results`, one per family |
| 14 | Past runs | — | `#earlier` |

The dominant problem is exactly the research's "page mirrors data structure" smell: the 8 probe tables exist because the data is grouped by family, not because the reader asks eight separate questions. Eight identical column sets = eight re-learned reading grids.

## 2. Q1 — do we need this many tables? Can they merge without losing IA?

**Yes, and no information is lost.** Merge targets, in order of value:

1. **The 8 per-family probe tables → ONE merged table** with a `family` grouping column and a `role` marker (incumbent vs candidate). Columns are byte-identical across all 8, so this is a pure concatenation — zero column loss. IA improves: cross-family comparison ("is zai faster than qwen?") becomes a scan instead of a scroll-and-hold.
2. **Failures (4) + official (3) stay collapsed** — they're already in `<details>` (progressive disclosure), which is the correct research pattern. Not everything must be visible; it must be *findable*.
3. **Verdict ticket stays as the hero.** It is already the Level-1 health view research prescribes (one route, one number, one decision). Don't touch it.

Result: **13 surfaces → 1 hero + 1 merged ranked table + 3 collapsed evidence tables.** Visible-by-default table count drops from ~10 to 2.

## 3. Q2 — how should the info be sorted?

Research consensus: group by *question*, not by data type; 3-level hierarchy; ≤5–6 primary items per group. Mapped to this project's four standing questions:

| Question | Surface | Sort |
|---|---|---|
| Is my default healthy? | Verdict ticket (hero) | — |
| What should I run / is there better? | **Merged probe table** | family → role (incumbent first, then candidates by predicted advantage) → eff $/M ascending; incumbent bar row highlighted; IQ per $ as tiebreak |
| What is it costing? | Cost per M table | realized eff $/M ascending (money view answers money question) |
| Is it fast? | Main-traffic speed | reqs descending (traffic weight first), ttft p50 secondary |

Key rule from the research: *position encodes importance* — the incumbent row sits directly under its family header, candidates sorted by advantage, the bar value rendered inline so "under/over bar" is readable without mental math.

## 4. Q3 — navigability improvements

1. **Per-route drill-down links** — `dist/checks/core.html` / `cache.html` already exist but nothing links to them from the merged table rows. A click on a route should open its evidence. (This is the research's Level-2 → Level-3 drill-down, already built, just unplumbed.)
2. **Family anchors + sticky nav** — nav currently has 4 section anchors. With one merged table, add per-family anchor jumps (qwen / glm / gpt / kimi) so "show me my family" is one tap.
3. **Client-side sort/filter** on the merged table (tiny JS, same pattern as existing `board.js`): sort by ttft / tps / $ / IQ; filter: board-only / candidates-only.
4. **Past runs collapse** — show latest N runs, "show all" link; today it renders every run group unconditionally (12 KB of history before method).
5. **Mobile** — merged table must drop horizontal scroll: fold low-priority columns (window, ask in/out) under the row (expandable), keep tests/cache/ttft/tps/eff visible.

## 5. Research sources

- UXMagic "Dashboard UI Design: Patterns and Best Practices" (2026-08) — Rule of 6, decision-metrics-only, progressive disclosure.
- UITOP "Dashboard Design Patterns for Data-Heavy SaaS" (2026-05) — primary/secondary/tertiary hierarchy, tables for detail, filters for depth.
- easy.bi "Why Your Dashboard Confuses Users" (2026-03) — 3-level KPI hierarchy, drill-down +20–40% task completion, "table masquerading as a dashboard" anti-pattern.
- Rework "Dashboard Design That Drives Decisions" (2026-04) — ≤5 primary metrics, hero 3× rule, position encodes importance.
- Medium/UXTBE "Designing UI for Dense Data" (2026-03) — group by question not chart type, mobile density rules.
