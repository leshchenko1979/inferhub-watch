"""Cache twin: repeats the core payload byte-for-byte and asks whether the
second identical request hit the prompt cache. One request, one verdict —
the core payload already carries tools and the 2048-token Russian chronicle,
so no extra prefix requests are needed (that is the suite-v2 win)."""

from __future__ import annotations

from probe.http import InferHubClient
from probe.payloads import core_payload
from probe.result import http_preview, result
from probe.sse import (
    cached_tokens,
    last_usage,
    parse_sse,
    resolved_model,
    usage_pricing_fields,
)


def run(client: InferHubClient, alias: str) -> dict:
    payload = core_payload(alias)
    status, raw, ms = client.post(payload)
    if status != 200:
        return result(
            check_id="cache",
            alias=alias,
            status="error",
            summary=http_preview(status, raw),
            http_status=status,
            latency_ms=ms,
        )
    chunks = parse_sse(raw)
    resolved = resolved_model(chunks, alias)
    usage = last_usage(chunks)
    cached = cached_tokens(usage)
    prompt = usage.get("prompt_tokens")
    prompt = prompt if isinstance(prompt, int) else None
    evidence = {
        "cached_tokens": cached,
        "prompt_tokens": prompt,
        "chunk_count": len(chunks),
        "usage": usage_pricing_fields(usage),
    }
    if prompt:
        evidence["hit_ratio"] = cached / prompt
    else:
        evidence["hit_ratio"] = 0.0
    if not chunks:
        return result(
            check_id="cache",
            alias=alias,
            status="fail",
            summary="No SSE JSON chunks in the stream.",
            resolved_model=resolved,
            http_status=status,
            latency_ms=ms,
            evidence=evidence,
        )
    if cached <= 0:
        if prompt is not None:
            miss = (
                f"First identical repeat showed no prompt-cache hit "
                f"({prompt} input tokens): usage.cached_tokens stayed 0."
            )
        else:
            miss = (
                "First identical repeat showed no prompt-cache hit: "
                "usage.cached_tokens stayed 0."
            )
        return result(
            check_id="cache",
            alias=alias,
            status="fail",
            summary=miss,
            resolved_model=resolved,
            http_status=status,
            latency_ms=ms,
            evidence=evidence,
        )
    if prompt is not None:
        hit = (
            f"Prompt cache hit on the identical repeat: "
            f"{cached} of {prompt} input tokens reused ({evidence['hit_ratio']:.0%})."
        )
    else:
        hit = f"Prompt cache hit on the identical repeat: {cached} input tokens reused."
    return result(
        check_id="cache",
        alias=alias,
        status="pass",
        summary=hit,
        resolved_model=resolved,
        http_status=status,
        latency_ms=ms,
        evidence=evidence,
    )