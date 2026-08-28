# Prompt cache on an identical repeat

This check is the **cache twin**: it sends the exact same request as the core probe — `stream: true`, `tool_choice: required`, the same `report_answer` tool, the same ~2k-token Russian chronicle — and asks whether the repeat hit the prompt cache.

There is no separate prefix request. The core payload already carries the tokens a cache needs to bite (text dense enough for ali's floor), so suite v2 folds "russian" and "tools" into request one and measures the cache on request two. The route is probed twice per day — or once, when the core probe already failed (fail-fast).

## Pass

The repeat reports a cache hit: `cached_tokens`, `prompt_tokens_details.cached_tokens`, `prompt_cache_hit_tokens`, or `cache_read_input_tokens` greater than zero.

## Fail

The identical repeat still reports `cached_tokens` 0, or the stream has no SSE chunks. This is a single attempt — the old "three tries with a prefix" dance is retired; if the route cannot cache a 2048-token repeat, that is already the answer.

## What we record

`cached_tokens`, `prompt_tokens`, `hit_ratio` (cached ÷ prompt), and the full pricing-shaped usage fields (`cost`, `market_cost`, `gateway_cost`, `credit` …) when the gateway reports them — the pricing observability of the retired usage-pricing check rides free on this request.