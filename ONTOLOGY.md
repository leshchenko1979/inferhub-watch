# inferhub-watch ontology

Single source of truth for domain vocabulary. Every code identifier and every
piece of site copy must use these terms exactly; the banned-synonyms table is
enforced by `tests/test_ontology.py`.

## Entities

| Term | Definition | Realized as |
|---|---|---|
| **Route** | One `publisher/model` pair (`ali/kimi-k3`) — the atomic unit. Every probe, ask, hit rate, and failure count belongs to exactly one route | `route` key in pricing rows; board rows keyed by it |
| **Publisher** | The prefix before `/` in a route (`ali`, `cx`, `cbcn`, `zai`) | `probe/market.py::family` conventions; `publishers` section |
| **Probe** | One *billed* measurement request against a route — costs real money. Dispatched by the sweep or on explicit owner OK only | `probe/` package; `probe/payloads.py` |
| **Sweep** | The scheduled full run (06:00Z): probe every route, pull the usage-log window, regenerate all data files | `.github/workflows/watch.yml` |
| **Board** | The main pricing table — one row per traffic-carrying route | `site/generate.py::pricing_section`, `#pricing` |
| **Window** | The 30-day usage-log pull that feeds all aggregates (tokens, billed asks, hit rates, failures) | `window` / `span` labels; `aggregate_rows` input |
| **Snapshot** | Dated `data/pricing/YYYY-MM-DD.json` — one history point per day | `probe/pricing.py::snapshot`, `site/rundata.py` |
| **Catalog snapshot** | `data/catalog.json` — the fetched `/api/catalog` model list with official prices and price points | `probe/catalog.py`, `site/rundata.py::load_catalog` |
| **Sparkline** | The inline-SVG ask-history graph under each board route's ask | `site/generate.py::_ask_spark`, `.ask-spark` |
| **Ghost route** | A zero-traffic route — hidden from the board until its first billed traffic | `ghost/route` in tests; `comparison_rows` skip logic |

## Prices

| Term | Definition | Realized as |
|---|---|---|
| **Ask** | $/Mtok actually billed on a route. Two sources, strictly ranked: **billed ask** (from usage logs — real money, always wins) over **floor ask** (cheapest catalog price point; rendered with `*` when a route has no billed traffic yet) | `ask_in` / `ask_out`; `*` mark title "floor ask" |
| **Blended eff** | Cache-adjusted effective price of a route: per-M input+output blended with the route's real hit rate | `official_compare.blended_eff` |
| **Hit rate** | Cached fraction of input tokens on a route | `official_compare._hit_rate` |
| **Cache rule** | The invariant: cached input bills at flat **10% of the input ask**, every route, every era. Verified per-row at snapshot time as `hit_ask_ratio` | `catalog.CACHE_RATE`, `cache_rule_stats` |
| **Drift flag** | Fires when a route's `hit_ask_ratio` wanders beyond ±0.02 from 0.1 — the cache rule is breaking for that route | `official_compare.drift_flag` |
| **Official ask** | Vendor list price for a route (`officialIn` / `officialOut` from the catalog) | `official_compare.official_eff` |
| **Multiplier** | Official ÷ here, cache-adjusted — the "20×" style verdict numbers | official-comparison table rows |
| **Projection** | The forward-looking cost line: the window's real token volume rerun at **today's** asks, inferhub vs official. Past volume × present prices — deliberately not "what I spent" (that mixes price eras and goes stale) | official-comparison projection `<p>` |
| **Floor ask** | The `*`-marked ask: catalog minimum, shown only when the route has no billed traffic in the window | `ask-mark` span; legend |

## Failures

| Term | Definition | Realized as |
|---|---|---|
| **Attempt** | One request row in the window, success or failure | `reqs` counts |
| **Failure** | One attempt that failed (usage-log `status`/`http_status` non-2xx or `status="failed"`). Failed rows carry zero tokens and zero cost | `probe/pricing.py::failure_stats` |
| **Failure stats** | Per-route failed / attempts / fail % / codes — the reliability sub-table inside `#pricing` | `site/generate.py::failures_table`, `payload["failures"]` |

## Invariants

1. **Billed ask beats floor ask.** A route with billed traffic never shows a
   catalog price; `*` appears only in the absence of traffic.
2. **Cached input = 0.1 × input ask (±0.02).** Any drift is flagged, never
   silently absorbed.
3. **Failures cost nothing.** Failed rows contribute zero tokens and zero cost;
   the board's traffic column counts attempts and the failures table makes the
   gap honest.
4. **Nothing hardcoded.** Every number on the site is recomputed from live data
   each sweep — no historical figures baked into copy (anti-staleness law).

## Naming law

Code identifiers and site copy must mirror these terms. A concept gets ONE name
across code, data keys, and prose.

### Banned synonyms

| Banned | Use instead | Rationale |
|---|---|---|
| error(s) | **failure** | an attempt failed; "error" collides with HTTP code names (a 502 code is a *code*, the row is a *failure*) |
| errors block / `error_stats` | **failures** / `failure_stats` | data key + function renamed with the term |
| price | **ask** (what we're billed) / **official ask** (vendor list) | "price" is ambiguous between the two sides of the comparison |
| catalog list price / list price | **floor ask** | the `*` legend term |
| historical price / what I spent | **projection** (for the forward line) / **window** (for aggregates) | projections reprice from the latest sweep; spend history mixes price eras |
| hit ratio / cache rate (prose) | **hit rate** (route property) / **cache rule** (the 10% invariant) / `hit_ask_ratio` (the per-row solved value) | one name per concept |

### Copy conventions

- The comparison sides are **"here"** and **"official"** — keep both words in
  table headers and the projection line.
- The `*` legend reads: "floor ask — catalog minimum, no billed traffic yet".
- Failure table columns: `attempts`, `failed`, `fail %`, `codes`.
