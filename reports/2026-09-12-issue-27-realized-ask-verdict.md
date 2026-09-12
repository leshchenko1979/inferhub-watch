# Issue #27, item 4 — realized-ask sanity check: `cb/deepseek-v4.1-flash`

**Question asked:** is the realized (billed) ask data for `cb/deepseek-v4.1-flash`
trustworthy, or defective?

**Verdict: DEFECTIVE as a cross-route ranking input.** The realized ask is not a
market observation for this route — it is a fixed function of the list price, and
it prices the route at a level no publisher currently offers.

## Evidence (Postgres `usage_logs`, 48 h window, all figures from live queries)

| Measure | Value |
|---|---|
| Requests with a billed ask | 9,424 |
| Distinct billed ask values | 3 |
| Modal value (`ask_input_per_mtok`) | `0.000150` |
| Modal value (`ask_output_per_mtok`) | `0.000600` |
| Share of requests at the modal value | **98.79 %** (9,310 / 9,424) |
| `official_in` / `official_out` for the route | `0.15` / `0.60` |
| Modal value == `official_in / 1000` | **True** (exact, 6 dp) |
| Modal value == `official_out / 1000` | **True** (exact, 6 dp) |
| Present anywhere in the route's live ladder | **No** |
| Cheapest filled ladder point (live) | `0.004500 @ 1 publisher` |
| Live floor ÷ modal billed value | **30.0×** |

## Why that is a defect and not a cheap publisher

1. **It is a fixed function of the list price.** The modal billed ask is exactly
   `official_in / 1000` and its output companion exactly `official_out / 1000`,
   to six decimal places, across 9,310 requests. A market price tracks supply;
   this one tracks the rate card.
2. **It prices the route below every offer.** The route's live ladder has no
   point at `0.000150`; the cheapest filled point is `0.004500` (1 publisher).
   The billed ask is therefore 30× cheaper than anything a publisher offers on
   that route — a price no request could be served at.
3. **The signature is not universal, so it is not a global unit convention.**
   Only 3 of the 11 routes with ≥5 requests in the window sit at exactly
   `official / 1000` (`ag/gemini-3.7-flash-high`, `cb/deepseek-v4.1-flash`,
   `ag/gemini-3.8-flash-high`). Every other route bills between **0.8 % and
   27 %** of official with a spread of distinct market values. A per-1K-token
   convention applied to some routes and not others is an inconsistency, not a
   convention.
4. **It is exactly the engine of the #27 thrash.** `cb/deepseek-v4.1-flash` and
   `ag/gemini-3.8-flash-high` both carry the signature, and both are the routes
   the switcher flipped between at 07:15Z and 07:24Z. On the realized basis the
   scaled value makes a route look ~1000× cheaper than the rate card; on the
   floor basis it does not. That is the basis-dependent ranking inversion.

## Consequence for the switcher

Before the #27 fix, a realized basis could be selected for a comparison in which
one route's realized ask was a `/1000`-scaled rate-card value and the other's was
a market ask — two different units ranked against each other.

After the #27 fix:

- every candidate is ranked on **one** basis (`select_basis` / `apply_basis` in
  `scripts/auto_route_switch.py`), floor by default;
- the realized basis is only selected when **every** candidate carries ≥5
  realized samples, so a partially-populated realized set can no longer be
  compared against a floor-basis competitor;
- the floor itself is a single pinned ladder point (`probe/floor.py`), so the
  floor side of any comparison is the same number the catalog publishes.

The `/1000` value itself is **not** corrected here — it is an upstream data
question (which unit InferHub bills in for these three routes), and correcting
it locally would be guessing. It is reported for follow-up.

## Recommendation

Ask InferHub to confirm the unit of `ask_input_per_mtok` / `ask_output_per_mtok`
for `cb/deepseek-v4.1-flash`, `ag/gemini-3.7-flash-high` and
`ag/gemini-3.8-flash-high`. Until it is confirmed, treat any realized ask that
sits at exactly `official / 1000` as unverified, and do not let it alone select
the realized basis.
