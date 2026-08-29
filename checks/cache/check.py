"""Cache twin: repeats the core probe's prompt byte-for-byte and asks whether
the second identical request hit the prompt cache. One request, one verdict —
the core payload already carries tools and the ~800-token Russian chronicle,
so no extra prefix requests are needed (that is the suite-v2 win).

The twin sets ``stream_options.include_usage``: the OpenAI spec returns usage
on streamed responses ONLY when requested, and spec-strict upstreams stay
silent otherwise (the ali deepseek lesson — invisible usage scored as a
proven cache miss while gateway billing showed real hits). Some InferHub
routes 400 unknown params (the max_tokens precedent), so on HTTP 400 the twin
retries ONCE without stream_options — worst case is the old usage-blind
behaviour, never a worse one."""

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
    payload = core_payload(alias, include_usage=True)
    status, raw, ms = client.post(payload)
    usage_requested = True
    if status == 400:
        # Some routes 400 unknown params (the max_tokens precedent). Drop
        # stream_options and retry ONCE — worst case is the old usage-blind
        # behaviour, never a worse one.
        payload = core_payload(alias)
        usage_requested = False
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
        # False only when the route 400'd stream_options and we fell back —
        # usage was then NOT requested, so absence says nothing about caching.
        "usage_requested": usage_requested,
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