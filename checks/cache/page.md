# Prompt cache on an identical repeat

This check is the **cache twin**: it repeats the core probe's request — `stream: true`, `tool_choice: required`, the same `report_answer` tool, the same head-only Russian chronicle (~800 tokens) — plus `stream_options.include_usage`, and asks whether the repeat hit the prompt cache. Usage on a streamed response only arrives when asked for (OpenAI spec), and spec-strict upstreams stay silent otherwise; a route that 400s the param gets one fallback retry without it — worst case the old usage-blind behaviour.

There is no separate prefix request. The core payload already carries the tokens a cache needs to bite — a 2026-08-29 floor bisection proved every incumbent seat caches the ~800-token head in full — so suite v2 folds "russian" and "tools" into request one and measures the cache on request two. The route is probed twice per day — or once, when the core probe already failed (fail-fast).

## Pass

The repeat reports a cache hit: `cached_tokens`, `prompt_tokens_details.cached_tokens`, `prompt_cache_hit_tokens`, or `cache_read_input_tokens` greater than zero.

## Fail

The identical repeat still reports `cached_tokens` 0, or the stream has no SSE chunks. This is a single attempt — the old "three tries with a prefix" dance is retired; if the route cannot cache an ~800-token repeat, that is already the answer.

## What we record

`cached_tokens`, `prompt_tokens`, `hit_ratio` (cached ÷ prompt), the full pricing-shaped usage fields (`cost`, `market_cost`, `gateway_cost`, `credit` …) when the gateway reports them — the pricing observability of the retired usage-pricing check rides free on this request — and `usage_requested` (`false` only when the route 400'd `stream_options` and we fell back, so a missing usage block says nothing about caching). Runs before 2026-08-30 used a padded ~2048-token chronicle; each run's `prompt_tokens` says which payload era it belongs to, so hit ratios stay comparable only within an era.