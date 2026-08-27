from __future__ import annotations

from probe.http import InferHubClient
from probe.payloads import completion_payload
from probe.result import http_preview, result
from probe.sse import inspect_stream, last_usage, parse_sse, resolved_model


def usage_evidence(chunks: list[dict]) -> dict:
    """Token counts from the final usage chunk — cost matching needs these."""
    usage = last_usage(chunks)
    out = {}
    for key in ("prompt_tokens", "completion_tokens"):
        value = usage.get(key)
        if isinstance(value, int):
            out[key] = value
    return out


def run(client: InferHubClient, alias: str) -> dict:
    payload = completion_payload(alias, stream=True)
    status, raw, ms = client.post(payload)
    if status != 200:
        return result(
            check_id="stream_tools",
            alias=alias,
            status="error",
            summary=http_preview(status, raw),
            http_status=status,
            latency_ms=ms,
        )
    chunks = parse_sse(raw)
    resolved = resolved_model(chunks, alias)
    stats = inspect_stream(chunks)
    stats["usage"] = usage_evidence(chunks)
    if not chunks:
        return result(
            check_id="stream_tools",
            alias=alias,
            status="fail",
            summary="No SSE JSON chunks in the stream.",
            resolved_model=resolved,
            http_status=status,
            latency_ms=ms,
            evidence=stats,
        )
    if stats["empty_finish_chunks"] or stats["empty_name_chunks"]:
        bits = []
        if stats["empty_finish_chunks"]:
            bits.append(
                f'{stats["empty_finish_chunks"]} event(s) set finish_reason to ""'
            )
        if stats["empty_name_chunks"]:
            bits.append(f'{stats["empty_name_chunks"]} tool delta(s) set name to ""')
        return result(
            check_id="stream_tools",
            alias=alias,
            status="fail",
            summary="Not the OpenAI stream shape: " + "; ".join(bits) + ".",
            resolved_model=resolved,
            http_status=status,
            latency_ms=ms,
            evidence=stats,
        )
    if not stats["names"]:
        return result(
            check_id="stream_tools",
            alias=alias,
            status="fail",
            summary="Stream ended without a non-empty tool name.",
            resolved_model=resolved,
            http_status=status,
            latency_ms=ms,
            evidence=stats,
        )
    return result(
        check_id="stream_tools",
        alias=alias,
        status="pass",
        summary=f"Streamed a tool call named {', '.join(stats['names'])}.",
        resolved_model=resolved,
        http_status=status,
        latency_ms=ms,
        evidence=stats,
    )
