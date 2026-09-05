"""Cache-vs-prompt-size ramp: find each route's minimum cacheable prefix.

Owner order 2026-09-05: "try to increase prompt size to find out when cache
would start working to the models that currently fail this test."

Method per (alias, size): send the core chronicle user prompt padded with N
deterministic pad blocks, TWICE byte-for-byte (prime, then measure). The
second request's usage.cached_tokens / prompt_tokens is the hit ratio. Two
requests per cell, sequential, so the prime is warm.

Sizes bill real tokens on both requests — keep the grid tight and sequential.

Usage: INFERHUB_API_KEY must be in env (run via the keys.toml pattern —
never print the key).
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from probe.http import InferHubClient
from probe.payloads import CORE_HEAD, _PAD, REPORT_ANSWER_TOOLS
from probe.sse import cached_tokens, last_usage, parse_sse

ALIASES = [
    "cbcn/glm-5.3-flash",
    "cb/gpt-5.6-luna",
    "cb/gpt-5.6-sol",
    "cb/gpt-5.6-terra",
    "cb/kimi-k3",
    "cp/zai/glm-5.3-flash",
]

# 0 = head only (~550-800 ali tokens billed); each block ≈ 65-80 tokens.
BLOCKS = [0, 4, 8, 12, 24, 48, 96]


def build_user(blocks: int) -> str:
    return CORE_HEAD + "".join(_PAD.format(i) for i in range(blocks))


def payload(alias: str, blocks: int) -> dict:
    return {
        "model": alias,
        "messages": [{"role": "user", "content": build_user(blocks)}],
        "tools": REPORT_ANSWER_TOOLS,
        "tool_choice": "required",
        "stream": True,
        "stream_options": {"include_usage": True},
    }


def measure(client: InferHubClient, alias: str, blocks: int) -> dict:
    pl = payload(alias, blocks)
    # Prime (result ignored beyond status).
    status, raw, _ = client.post(pl)
    if status != 200:
        return {"blocks": blocks, "error": f"prime HTTP {status}"}
    time.sleep(1.0)
    status, raw, ms = client.post(pl)
    if status != 200:
        return {"blocks": blocks, "error": f"measure HTTP {status}"}
    chunks = parse_sse(raw)
    usage = last_usage(chunks)
    cached = cached_tokens(usage)
    prompt = usage.get("prompt_tokens")
    row = {
        "blocks": blocks,
        "prompt_tokens": prompt if isinstance(prompt, int) else None,
        "cached_tokens": cached,
    }
    if isinstance(prompt, int) and prompt:
        row["hit_ratio"] = round(cached / prompt, 4)
    row["latency_ms"] = round(ms)
    return row


def main() -> None:
    import os

    key = os.environ.get("INFERHUB_API_KEY", "").strip()
    if not key:
        print("INFERHUB_API_KEY is required", file=sys.stderr)
        raise SystemExit(2)
    client = InferHubClient(key)
    results: dict[str, list] = {}
    for alias in ALIASES:
        rows = []
        for blocks in BLOCKS:
            row = measure(client, alias, blocks)
            rows.append(row)
            print(f"{alias} blocks={blocks}: {json.dumps(row)}", flush=True)
            time.sleep(1.0)
        results[alias] = rows
    out = Path(__file__).resolve().parent.parent / "data" / "cache_ramp_20260905.json"
    out.write_text(json.dumps(results, indent=1, ensure_ascii=False))
    print(f"saved {out}")


if __name__ == "__main__":
    main()
