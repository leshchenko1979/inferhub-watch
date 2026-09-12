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
| **Sweep** | The scheduled full run (02:00Z): probe every route, pull the usage-log window, regenerate all data files | `.github/workflows/watch.yml` |
| **Board** | The main pricing table — one row per traffic-carrying route; rows sort by **$/M ascending** (cheapest first — the board answers "what is this costing me") on a **gated basis**: the realized 30-day cost while the projection gate fails, the **projected** cost once it passes. Ties break by **IQ per $** descending; routes without an eff figure last | `site/board.py::_iq_sort_key`, `_board_basis` (`use_proj = gate["pass"]`); `site/generate.py::pricing_section` |
| **Board Cost Crown** | The route the board ranks **first** under that gated basis — the cheapest $/M, with IQ per $ only the tiebreak inside an equal cost band. Answers "what is this costing me" and is the unit the projection gate backtests: for each consecutive snapshot pair, the crowned route's realized cost is compared with the true cheapest realized cost | `probe/official_compare.py::_crown_regrets`; `site/board.py::_iq_sort_key` |
| **Switcher Value Crown** | The active fleet winner selected by the automated switcher based on empirical Value: `Value = (IQ / eff_price) * (tps / tps_ref) ** 0.50`, where `eff_price` matches the board crown basis (`probe/basis.py::board_basis` with cold-start fleet prior fallback) after applying orthogonal constraints (IQ floor >= 35.0, tool support, DeepSeek date guard). Swaps fleet route when challenger exceeds incumbent by >15% | `scripts/auto_route_switch.py::find_best_route` |
| **Crown** | Collective term for the winning route: Board Cost Crown (cost minimizer) and Switcher Value Crown (efficiency + capability optimizer) | `site/board.py`, `scripts/auto_route_switch.py` |
| **Verdict** | The decision ticket at the top of the page: the best IQ-per-$ route right now (billed ask beats floor ask when picking), why it wins (ask streak, cache share), and the runner-up as the alternate. Never hardcoded — recomputed every sweep | `site/generate.py::verdict_section`, `#verdict` |
| **Plumbing** | The per-route detail row folded under the board decision columns (Δ ask, ask source, ask history, cache hit, failures, window traffic and cost, ttft/tps, IQ, retries). Opens as a sibling table row via the "more" chip; the decision surface stays four columns | `site/board.py::_plumb_row`, `tr.plumb-row` |
| **Window** | The 30-day usage-log pull that feeds all aggregates (tokens, billed asks, hit rates, failures) | `window` / `span` labels; `aggregate_rows` input |
| **Snapshot** | Dated `data/pricing/YYYY-MM-DD.json` — one history point per day | `probe/pricing.py::snapshot`, `site/rundata.py` |
| **Catalog snapshot** | `data/catalog.json` — the fetched `/api/catalog` model list with official prices and price points | `probe/catalog.py`, `site/rundata.py::load_catalog` |
| **Sparkline** | The inline-SVG ask-history graph under each board route's ask | `site/generate.py::_ask_spark`, `.ask-spark` |
| **Ghost route** | A zero-traffic route — hidden from the board until its first billed traffic | `ghost/route` in tests; `comparison_rows` skip logic |
| **Candidate** | A catalog route predicted cheaper than its family's incumbent bar — shortlisted by the radar for the next sweep's probe. Parking law: the 7-day proven freeze is earned only by a conclusively **passing** probe; a failed probe re-enters the shortlist when its worst-case (zero-cache) prediction still beats the bar — a cache problem must not bury a genuinely cheaper ask | `probe/market.py::shortlist`, `_pick`, `worst_case_predicted` |

## Prices

| Term | Definition | Realized as |
|---|---|---|
| **Ask** | $/Mtok actually billed on a route. Two sources, strictly ranked: **billed ask** (from usage logs — real money, always wins) over **floor ask** (cheapest catalog price point; rendered with `*` when a route has no billed traffic yet) | `ask_in` / `ask_out`; `*` mark title "floor ask" |
| **Blended eff** | Cache-adjusted effective price of a route: per-M input+output blended with the route's real hit rate. For routes without billed history, fallback weights use the fleet empirical ratio of 99.4% input / 0.6% output (`w_in=0.99, w_out=0.01`), reflecting agentic multi-turn prompt loads | `official_compare.blended_eff`, `models.toml [market].fallback_w_in` |
| **Hit rate** | Cached fraction of input tokens on a route | `official_compare._hit_rate` |
| **Cache rule** | The invariant: cached input bills at flat **10% of the input ask**, every route, every era. Verified per-row at snapshot time as `hit_ask_ratio` | `catalog.CACHE_RATE`, `cache_rule_stats` |
| **Drift flag** | Fires when a route's `hit_ask_ratio` wanders beyond ±0.02 from 0.1 — the cache rule is breaking for that route | `official_compare.drift_flag` |
| **Official ask** | Vendor list price for a route (`officialIn` / `officialOut` from the catalog) | `official_compare.official_eff` |
| **Multiplier** | Official ÷ here, cache-adjusted — the "20×" style verdict numbers | official-comparison table rows |
| **Projection** | The forward-looking cost line: the window's real token volume rerun at **today's** asks, inferhub vs official. Past volume × present prices — deliberately not "what I spent" (that mixes price eras and goes stale) | official-comparison projection `<p>` |
| **Median ask** | The billed ask over the newest 7 days of log rows (median — robust to single-row outliers), not just the last row; the window collapse only when no row has a parseable ts | `pricing._median_ask`, `ASK_MEDIAN_DAYS` |
| **Smoothed hit rate** | The route's hit rate projected forward: EWMA over daily snapshots (live window enters last) shrunk toward the fleet prior while the route's own evidence is thin | `official_compare.projection_hit`, `HIT_EWMA_ALPHA`, `SHRINK_K` |
| **Projection gate** | The crown backtest that decides when the forward view is trusted: for each consecutive pair of dated snapshots, take the route the board would have crowned on day t (lowest projected $/M — the board's primary sort), look up that route's REALIZED cost in the following window, and compare it to the true cheapest realized cost in that window. A transition LANDS when the crowned route's realized cost is within `GATE_TOL` (15%) of the true cheapest. Flips IQ/$ ranking + verdict + board sort onto the projection only when at least `GATE_MIN_N` transitions exist and `GATE_SHARE` of them land. Measures the severity of a wrong crown, not a whole-ranking correlation — the crown is what the switcher acts on. Recomputed per render — never hardcoded | `official_compare.projection_gate` |
| **Marginal realized** | Realized $/M over usage rows strictly since the previous snapshot — the fair forward comparator the gate will be judged against once ~2 weeks accumulate; realized-over-30d smears price changes, this slice does not. Dimmed to probe-only when every fresh request for the route falls inside a sweep window (real money, unrepresentative cache-cold workload) | `pricing.prior_snapshot_cutoff`, `pricing.marginal_stats`, `generate._probe_only` |
| **Floor ask** | The `*`-marked ask: catalog minimum, shown only when the route has no billed traffic in the window | `ask-mark` span; legend |
| **Intelligence (IQ)** | Artificial Analysis Intelligence Index for a route's model — composite of 9 public evals, fetched fresh from artificialanalysis.ai every sweep into `data/intelligence.json`; effort level pinned to (max). Never hardcoded | `probe/intelligence.py`, models.toml `[aa]` slug map |
| **IQ per $** | Intelligence ÷ the route's effective $/M — the smarter-per-dollar verdict column. Higher is better | board `IQ per $` column |
| **TPS Weight** | Empirical throughput multiplier on Value: square-root power-law `(tps / tps_ref) ** 0.50` using rolling 24h production `tps_mean` ($n \ge 5$) or sustained qualification probe (`probe/tps_qual.py`), normalized against the dynamic fleet median prior `probe/official_compare.py::fleet_tps_prior` | `scripts/auto_route_switch.py::calculate_value`, `probe/tps_qual.py` |
| **Minimum charge floor** | Minimum charge of **$0.000010** (10 microcents) per successful request enforced by Inferhub when calculated cost is below $0.000010 | `cost_usdc` lower bound in `usage_logs` |

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
5. **Minimum charge floor ($0.000010 / request).** Inferhub billing enforces a
   minimum charge of $0.000010 per successful request regardless of how few
   tokens were processed or how high the cache hit rate was.

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
