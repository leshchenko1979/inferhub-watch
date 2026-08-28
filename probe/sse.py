from __future__ import annotations

import json
from typing import Any


def parse_sse(raw: str) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for line in raw.split("\n"):
        stripped = line.strip()
        if not stripped.startswith("data:"):
            continue
        data = stripped[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            chunks.append(json.loads(data))
        except json.JSONDecodeError:
            continue
    return chunks


def tool_deltas(chunk: dict[str, Any]) -> list[dict[str, Any]]:
    choices = chunk.get("choices") or []
    if not choices:
        return []
    choice = choices[0]
    delta = choice.get("delta") or {}
    message = choice.get("message") or {}
    return delta.get("tool_calls") or message.get("tool_calls") or []


def finish_reason(chunk: dict[str, Any]) -> Any:
    choices = chunk.get("choices") or []
    if not choices:
        return None
    return choices[0].get("finish_reason")


def resolved_model(chunks: list[dict[str, Any]], fallback: str = "") -> str:
    for chunk in chunks:
        model = chunk.get("model")
        if isinstance(model, str) and model:
            return model
    return fallback


def last_usage(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    for chunk in reversed(chunks):
        usage = chunk.get("usage")
        if isinstance(usage, dict) and usage:
            return usage
    return {}


def inspect_stream(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    """OpenAI streaming contract, scored to the consuming runtime's parity.

    Empty-string finish_reason and empty-string tool names are both counted;
    the CORE CHECK decides the verdict: finish_reason "" fails (terminal to
    the consumer's accumulator), name "" is tolerated evidence only."""
    empty_names = 0
    empty_finish = 0
    names: list[str] = []
    last_finish: Any = None
    for chunk in chunks:
        reason = finish_reason(chunk)
        if reason == "":
            empty_finish += 1
        if reason is not None:
            last_finish = reason
        for tc in tool_deltas(chunk):
            func = tc.get("function") or {}
            name = func.get("name")
            if name == "":
                empty_names += 1
            if isinstance(name, str) and name:
                names.append(name)
    unique = list(dict.fromkeys(names))
    return {
        "chunk_count": len(chunks),
        "empty_name_chunks": empty_names,
        "empty_finish_chunks": empty_finish,
        "names": unique,
        "last_finish_reason": last_finish,
    }


def usage_pricing_fields(usage: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "cost",
        "market_cost",
        "gateway_cost",
        "credit",
        "is_byok",
        "cost_details",
        "prompt_tokens",
        "completion_tokens",
        "cached_tokens",
        "prompt_cache_hit_tokens",
        "prompt_cache_miss_tokens",
    )
    out = {k: usage[k] for k in keys if k in usage}
    details = usage.get("prompt_tokens_details") or {}
    if isinstance(details, dict) and details.get("cached_tokens") is not None:
        out["cached_tokens"] = details.get("cached_tokens")
    return out


def _int_tokens(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


def cached_tokens(usage: dict[str, Any]) -> int:
    details = usage.get("prompt_tokens_details")
    if not isinstance(details, dict):
        details = {}
    return max(
        _int_tokens(usage.get("cached_tokens")),
        _int_tokens(usage.get("prompt_cache_hit_tokens")),
        _int_tokens(usage.get("cache_read_input_tokens")),
        _int_tokens(details.get("cached_tokens")),
        _int_tokens(details.get("cache_read_input_tokens")),
        _int_tokens(details.get("prompt_cache_hit_tokens")),
    )
