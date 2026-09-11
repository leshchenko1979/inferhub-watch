# InferHub Watch — reading guide

One page for all explanatory text. The board keeps only live state
(numbers, gate booleans, chips) and links here; nothing on the docs page
is hardcoded data — numbers belong to the board.

## How to read the board

### Cost per M tokens (the pricing table)

"ask" under each route is the per-M rate billed on fresh (uncached)
input / output; "effective" is billed cost over all tokens, cache
discounts included. Each rate cell pairs the two eras and tags each:
"now" is the forward projection at current billed asks (median ask,
smoothed hit rate) and "30d" the realized window figure — the bold one
is the basis IQ per $ ranks on.

Effective bars are log-scaled from the cheapest to the priciest route on
the board (priciest = full bar) and colored teal ≤ $0.02, amber above.

The rate basis is decided by the projection rank gate: when the gate
passes, IQ per $ ranks on the "now" projection; when it does not, ranks
on the realized "30d" figure. The board's caption carries the live gate
state (transitions landed at the rank bar) on every sweep.

"Show plumbing" folds each route's ask movement (the color key sits
under the table), ask source, ask history, cache hit, failures, window
traffic and cost, ttft p50 and tps mean (main-traffic speed, newest
24h), IQ, and retries (when a sweep replayed a route).

Marginal $/M is billed cost over requests since the previous daily
snapshot, dimmed when the route's only fresh traffic is sweep probes
(real money, unrepresentative workload).

* = floor ask — catalog minimum, shown when the route has no billed
traffic in the window. The `*` legend reads: "floor ask — catalog
minimum, no billed traffic yet".

IQ = Artificial Analysis Intelligence Index (composite of 9 public
evals, artificialanalysis.ai, effort level max), refreshed every sweep;
IQ per $ divides it by the route's effective $/M — higher is smarter
per dollar.

Sparkline bars are log-scaled $0.001–$10 per day.

Rates cover this board's routes, the current candidates, and every
route with billed usage in the window.

### Δ ask color key

Δ ask in / out vs the previous daily snapshot: ↓ cheaper · ↑ pricier ·
— no earlier snapshot. In = prompt tokens, out = completion tokens.

### The scatter chart

Marginal $/M vs AA IQ, in use = billed traffic in the newest 24h.
Right = cheaper, up = smarter. Log x-axis; eff fallback for routes
without a marginal sample; routes without a price or an IQ mapping are
not plotted (the caption counts the skipped).

Dot color: teal = in use (100+ billed reqs/24h), amber = probes only,
gray = little or no traffic. Dot size tracks 24h traffic.

### Probe results (the candidates table)

Board routes in current use ("in use" pill) plus audition routes from
the market shortlist — live catalog asks ranked by predicted $/M,
cheaper-than-in-use only — probed after each board sweep. One merged
table: family bands in reading order, incumbent first, then audition
routes by checks passed, cache hit, blended ask.

Cache share: board routes from the 30-day billing window, audition
routes from the probe. Window = runs all-pass / runs probed since first
seen. Audition asks are billed on probe traffic.

## How we test

### The endpoint

[InferHub](https://inferhub.dev/) exposes an [OI]-compatible
`/v1/chat/completions` API. This site probes that endpoint daily with
`stream: true`. A failed test score means a check missed in that run. It
is not an uptime check and not a score of your traffic.

### The results table

- **Route**: the short name we send in `model`; the publisher line
  underneath is the `model` string InferHub returned (who actually
  served). The two can differ.
- **tests**: scoring checks passed in the latest probe — tool names in
  the SSE stream, prompt cache, and mojibake in answers to a Russian
  question. Missed checks are named under the score.
- **cache hit / ask / window**: billing-window cache share (board
  routes) or probe evidence (audition routes), current ask prices, and
  the all-pass record since the route was first seen.
- **Info checks**: recorded on each check page; they never fail a route.
  Price is info and never ranks.

### Each check

Open a check page for the request JSON and the pass/fail rule. The
board's check links and the list below point at the same pages.

<!-- CHECK_EXPLAINERS -->

### Ask for another

To probe another InferHub alias,
[open a GitHub issue](https://github.com/leshchenko1979/inferhub-watch/issues/new)
with the name you send in `model`. The current list is
[models.toml](https://github.com/leshchenko1979/inferhub-watch/blob/main/models.toml).
To add a check, describe the request JSON and what should pass or fail.
Pull requests follow the README
[Add a check](https://github.com/leshchenko1979/inferhub-watch/blob/main/README.md#add-a-check)
steps.
