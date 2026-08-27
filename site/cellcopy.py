"""Board cell notes: what happened, then the wire field. Works on stored evidence."""

from __future__ import annotations


def note(cell: dict) -> str:
    cid = cell.get("check_id") or ""
    status = cell.get("status") or ""
    evidence = cell.get("evidence") if isinstance(cell.get("evidence"), dict) else {}
    raw = (cell.get("summary") or "").strip()
    if cid == "stream_tools":
        return _tools(status, evidence, raw)
    if cid == "cache_tools":
        return _cache(status, evidence, raw)
    if cid == "ru_mojibake":
        return _ru(status, evidence, raw)
    if cid == "usage_pricing":
        return _price(status, evidence, raw)
    return raw


def _ru(status: str, ev: dict, raw: str) -> str:
    if status == "error":
        return raw or "The request did not return HTTP 200."
    if status == "pass":
        cyr = int(ev.get("cyrillic_chars") or 0)
        if cyr == 0:
            return (
                "No mojibake, but the answer contained no Cyrillic — "
                "not recognizably Russian."
            )
        return (
            "Russian answer arrived clean — no replacement characters, "
            "no double-encoded Cyrillic, no CJK flood."
        )
    if "No SSE" in raw:
        return "The stream contained no JSON events, so there was nothing to parse."
    return raw


def _tools(status: str, ev: dict, raw: str) -> str:
    names = [n for n in (ev.get("names") or []) if n]
    empty_finish = int(ev.get("empty_finish_chunks") or 0)
    empty_name = int(ev.get("empty_name_chunks") or 0)
    chunks = int(ev.get("chunk_count") or 0)
    if status == "error":
        return raw or "The request did not return HTTP 200."
    if status == "pass":
        called = ", ".join(names) if names else "a tool"
        return (
            f"Streamed a tool call named {called}. "
            "finish_reason was null or omitted, never an empty string."
        )
    if chunks == 0 or "No SSE" in raw:
        return "The stream contained no JSON events, so there was nothing to parse."
    bits = []
    if empty_finish:
        bits.append(
            f'{empty_finish} event(s) set finish_reason to "" '
            "(OpenAI uses null or omits the field)"
        )
    if empty_name:
        bits.append(f'{empty_name} tool delta(s) set name to ""')
    if bits:
        return "Not the OpenAI stream shape: " + "; ".join(bits) + "."
    if not names:
        return (
            "Required a tool call, but no non-empty tool name appeared in the stream."
        )
    return raw


def _cache(status: str, ev: dict, raw: str) -> str:
    cached = ev.get("cached_tokens")
    usage = ev.get("usage") if isinstance(ev.get("usage"), dict) else {}
    prompt = usage.get("prompt_tokens")
    if status == "error":
        return raw or "The request did not return HTTP 200."
    if status == "pass":
        if prompt is not None and cached is not None:
            return f"Prompt cache hit: {cached} of {prompt} input tokens were reused."
        if cached is not None:
            return f"Prompt cache hit: {cached} input tokens reused."
        return raw or "Prompt cache hit."
    if "No SSE" in raw:
        return (
            "The stream contained no JSON events, so usage (and cache) never arrived."
        )
    if prompt is not None:
        return (
            f"No prompt-cache hit after 3 tries ({prompt} input tokens, no tools). "
            "usage.cached_tokens stayed 0."
        )
    return "No prompt-cache hit after 3 tries (no tools). usage.cached_tokens stayed 0."


def _price(status: str, ev: dict, raw: str) -> str:
    if status == "error":
        return raw or "The request did not return HTTP 200."
    usage = ev.get("usage") if isinstance(ev.get("usage"), dict) else {}
    bits = [
        f"{k}={usage[k]}"
        for k in ("cost", "market_cost", "gateway_cost", "credit")
        if k in usage
    ]
    if bits:
        return (
            "Price on this stream: "
            + ", ".join(bits)
            + ". Info only; not part of Safe to use."
        )
    return (
        "This stream had no cost or credit field. Info only; not part of Safe to use."
    )
