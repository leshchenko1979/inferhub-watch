# Architecture Critique — inferhub-watch (subagent arch-critique, 2026-09-07)

## 1. Architecture map

Three-stage, file-artifact pipeline, no runtime coupling:
(a) **collect** — watch.yml runs probe.run (SSE probes via checks/{core,cache}/check.py plugins loaded by probe/registry), then probe.pricing/catalog/intelligence, writing immutable JSON artifacts to data/ and committing to git.
(b) **decide** — probe/market.py shortlists cheaper candidates per family (parking law in _classify, proven-cache TTL in data/proven.json); probe/costs.py attributes realized billed cost from /usage/logs onto probe cells; probe/radar.py compares passing challengers vs incumbents and pings.
(c) **render** — site/generate.py is a pure offline build (no API key; the PR validate job proves it), reads only committed data/*.json via site/rundata.py, emits the zero-dependency HTML board. Money math shared by probe and site layers lives in probe/pricing.py and probe/official_compare.py.

## 2. Strengths

- **Collect/render seam genuinely clean** — site build calls zero APIs, works from committed artifacts; makes the site testable and reproducible. Best structural decision in the repo.
- **Cost truth architecturally enforced** — costs.py refuses to guess (unmatched cells get no cost; weak time-order match guarded by len(pool)==len(cells)); official_compare.drift_flag audits the cache rule against billed rows. Honesty mechanisms (floor-ask asterisk, probe-only dimming, backtest gate) are structural.
- **Dedup discipline where it matters** — parking law one copy (market._classify), family grouping one copy (market.board_families), aa_slug one copy (probe/registry), with why-comments.
- **Checks are plugins** — tiny contract (module.run(client, alias) → result dict).
- **Serious test coverage** — 21 test files incl. money math, parking law, rate-limit retry, render contract tests, ontology tests.
- **CI hygiene** — Retry-After pagination, 429 backoff, stale-pricing annotations, rebase-retry push.

## 3. Weaknesses

- **W1 HIGH — silent 30d log-window truncation, active now.** pricing.py:41 MAX_PAGES=120 × PAGE_SIZE=100 = 12,000 rows; fetch_log_rows stops at cap with no flag. data/pricing.json requests_scanned == 12000 == exact cap (VERIFIED on live data 2026-09-07). Daily spend series, 30d route costs, failure stats, MTD figures computed over a clipped window; days beyond the 12,000th-newest row render as no traffic. Gets worse with traffic; nothing says so.
- **W2 HIGH — money basis decided in three places.** generate.py owns realized-vs-projection switch (_board_basis/_proj_eff, gated on projection_gate); market.incumbent_bar and radar._family_verdict rank on eff_per_mtok only — no gate, no projection, no marginal. Candidate shortlist, radar alert, and board ticket can price the same route differently on the same day. The boarding decision (the owner's deliverable) has no single price definition.
- **W3 MED-HIGH — generate.py is a 1,670-line god-module** (render + SVG + assembly + decision math). Scar tissue: dead _iq_sort_key block at generate.py:652-662 (superseded ranking law). Owner policy churn lands in the same file that draws sparklines.
- **W4 MED — duplicated concepts, divergent copies.** candidate_cache_pct (rundata.py:103 vs radar.py:46); parse_ts (costs.py:33 raises vs pricing.py:234 returns None, UTC assumption); load_pricing (market.py:96 cached vs rundata.py:262 validated); newest-run helper (pricing.py:471 vs radar.py:24); two money formatters (pricing.rate_label vs rundata.cost_label); daily_report._latest_snapshots reimplements rundata.load_dated_pricing.
- **W5 MED — schema evolution via permanent legacy fallbacks.** "failures or errors" fallbacks (comment says "remove after the sweep lands" — 2026-09-02; both copies remain), LEGACY_WINDOW in costs.py, legacy alias_models pooling, run_groups tolerating cells with no model key. No schema version on any artifact; temporary shims are structurally permanent.
- **W6 MED-LOW — aggregate snapshot carries raw per-request data.** marginal_stats embeds up to MARGINAL_TS_CAP=400 timestamps per route into pricing.json solely for generate._probe_only; past 400 silently degrades. Snapshot bloat (09-06/09-07 files ~42-52KB vs ~2KB earlier).
- **W7 LOW-MED — policy hard-coded in market.py.** FAMILY_ALIASES/DATED_FAMILIES are owner directives in code; belongs in models.toml next to [aa]. daily_report imports five underscore-private names from market.
- **W8 LOW — unbounded growth + redundant parsing.** load_runs parses every run JSON ~3× per build; probe_spend sums all runs ever; runs+snapshots committed forever. Sequential probing with inline sleep(20) widens the window as families×TOP_N×checks grow.

## 4. Top 3 recommendations

1. **Fix the silent truncation (small)** — fetch_log_rows returns a truncated flag (or compute pages from rangeTotal), record window_truncated in the snapshot, render a loud board badge; bump MAX_PAGES or slice range_ per month. ~20 lines + 1 test. Correctness bug in the owner's #1 priority, provably active today.
2. **One decision core for the money basis (medium)** — probe/basis.py owning per-route realized/marginal/projection + authoritative basis (gate state); board, ticket, scatter, incumbent_bar, radar all consume it. While in there: delete dead _iq_sort_key, collapse W4 duplicates.
3. **Split generate.py, move family policy to config (medium-large)** — board/ticket/scatter/evidence modules with a plain-data context; FAMILY_ALIASES/DATED_FAMILIES + screen thresholds into models.toml.

**Verdict:** fit for purpose for a single-operator daily probe dashboard — clean collect/render seam, honesty machinery is structural — but carrying one live correctness bug (W1), one decision-integrity split (W2), and a render god-module. Do rec 1–2 now, 3 within the month; no restructure needed.
