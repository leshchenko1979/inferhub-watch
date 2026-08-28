# InferHub Watch

Public daily probes of InferHub’s OpenAI-compatible Chat Completions API.

## Language

**Alias**:
The short model name we request (`gpt-5.6-luna`).
_Avoid_: “model” alone when the publisher prefix matters.

**Resolved publisher**:
The `model` string InferHub returns. On the board it sits under the alias (`ClinePass · cp/cline-pass/…`), not in its own column.

**Check**:
One registered experiment: `checks/<id>/check.py` plus `checks/<id>/page.md`.

**Pass / fail**:
Whether InferHub matched the shape that check documents.
_Avoid_: “InferHub is down.”

**Info**:
Recorded, never fails the suite (usage-based pricing in [data/pricing.json](data/pricing.json)).

**Scoring checks**:
`core` (stream shape + named tool call + Russian answer; fail-fast) and `cache` (identical repeat, `cached_tokens > 0`). Pricing does not score.
