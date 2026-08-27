from __future__ import annotations

import hashlib
import time

from probe.http import InferHubClient
from probe.payloads import cache_payload, cache_prefix
from probe.result import http_preview, result
from probe.sse import cached_tokens, last_usage, parse_sse, resolved_model

# InferHub sometimes reports the write on attempt 1 and the hit only after a beat.
RETRY_PAUSE_S = 2.0


def run(client: InferHubClient, alias: str) -> dict:
    prefix = cache_prefix()
    prefix_hash = hashlib.sha256(prefix.encode()).hexdigest()[:12]
    last = None
    cached = 0
    resolved = alias
    attempts_usage: list[dict] = []
    for attempt in range(3):
        if attempt and RETRY_PAUSE_S:
            time.sleep(RETRY_PAUSE_S)
        status, raw, ms = client.post(cache_payload(alias, prefix))
        if status != 200:
            return result(
                check_id="cache_tools",
                alias=alias,
                status="error",
                summary=http_preview(status, raw),
                resolved_model=resolved,
                http_status=status,
                latency_ms=ms,
                evidence={"prefix_hash": prefix_hash},
            )
        chunks = parse_sse(raw)
        resolved = resolved_model(chunks, resolved)
        usage = last_usage(chunks)
        attempts_usage.append(
            {
                key: usage.get(key)
                for key in ("prompt_tokens", "completion_tokens")
                if isinstance(usage.get(key), int)
            }
        )
        cached = cached_tokens(usage)
        last = (status, usage, ms, len(chunks))
        if cached > 0:
            break

    assert last is not None
    status, usage, ms, n_chunks = last
    evidence = {
        "prefix_hash": prefix_hash,
        "cached_tokens": cached,
        "chunk_count": n_chunks,
        "usage": usage,
        "usage_all": [u for u in attempts_usage if len(u) == 2],
        "had_tools": False,
    }
    if n_chunks == 0:
        return result(
            check_id="cache_tools",
            alias=alias,
            status="fail",
            summary="No SSE JSON chunks in the stream.",
            resolved_model=resolved,
            http_status=status,
            latency_ms=ms,
            evidence=evidence,
        )
    if cached <= 0:
        prompt = usage.get("prompt_tokens")
        if prompt is not None:
            miss = (
                f"No prompt-cache hit after 3 tries ({prompt} input tokens, no tools). "
                "usage.cached_tokens stayed 0."
            )
        else:
            miss = (
                "No prompt-cache hit after 3 tries (no tools). "
                "usage.cached_tokens stayed 0."
            )
        return result(
            check_id="cache_tools",
            alias=alias,
            status="fail",
            summary=miss,
            resolved_model=resolved,
            http_status=status,
            latency_ms=ms,
            evidence=evidence,
        )
    prompt = usage.get("prompt_tokens")
    if prompt is not None:
        hit = f"Prompt cache hit: {cached} of {prompt} input tokens were reused."
    else:
        hit = f"Prompt cache hit: {cached} input tokens reused."
    return result(
        check_id="cache_tools",
        alias=alias,
        status="pass",
        summary=hit,
        resolved_model=resolved,
        http_status=status,
        latency_ms=ms,
        evidence=evidence,
    )
