from __future__ import annotations

import re

from probe.http import InferHubClient
from probe.payloads import russian_payload
from probe.result import http_preview, result
from probe.sse import last_usage, parse_sse, resolved_model

CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")
# UTF-8 Cyrillic misread as Latin-1/CP1252: lead bytes 0xD0/0xD1 become Ð/Ñ,
# and every continuation byte lands in U+0080..U+00FF. "Ð¸Ð³Ð¾Ñ€ÑŒ" is the look.
U8_LEADS = "ÐÑ"
# CP1251 Cyrillic misread as Latin-1 lands entirely in U+00C0..U+00FF: "èãîðü".
MIN_CP1251_CHARS = 4
# UTF-8 bytes misread as GBK/Big5/Shift-JIS pair up into CJK: "叶青体" instead of
# Cyrillic. Kana and hangul included — any East-Asian flood is the same bug.
CJK_RE = re.compile(r"[\u3040-\u30FF\u3400-\u4DBF\u4E00-\u9FFF\uAC00-\uD7AF\uF900-\uFAFF]")
MIN_CJK_CHARS = 2


def text_content(chunks: list[dict]) -> str:
    parts: list[str] = []
    for chunk in chunks:
        for choice in chunk.get("choices") or []:
            piece = (choice.get("delta") or {}).get("content")
            if not isinstance(piece, str):
                piece = (choice.get("message") or {}).get("content")
            if isinstance(piece, str):
                parts.append(piece)
    return "".join(parts)


def last_finish_reason(chunks: list[dict]) -> object:
    reason = None
    for chunk in chunks:
        choices = chunk.get("choices") or []
        if choices and choices[0].get("finish_reason") is not None:
            reason = choices[0].get("finish_reason")
    return reason


def mojibake_stats(text: str) -> dict[str, int]:
    bigrams = 0
    for i in range(len(text) - 1):
        if text[i] in U8_LEADS and "\u0080" <= text[i + 1] <= "\u00FF":
            bigrams += 1
    return {
        "chars": len(text),
        "cyrillic_chars": len(CYRILLIC_RE.findall(text)),
        "replacement_chars": text.count("\uFFFD"),
        "u8_bigrams": bigrams,
        "accented_chars": sum(1 for ch in text if "\u00C0" <= ch <= "\u00FF"),
        "cjk_chars": len(CJK_RE.findall(text)),
    }


def mojibake_verdict(text: str) -> str | None:
    """Return a mojibake failure reason, or None when the text looks clean."""
    stats = mojibake_stats(text)
    if stats["replacement_chars"]:
        return (
            f'{stats["replacement_chars"]} U+FFFD replacement character(s) '
            "— bytes were not valid UTF-8."
        )
    if stats["cjk_chars"] >= MIN_CJK_CHARS:
        return (
            f'{stats["cjk_chars"]} CJK character(s) in a Russian answer '
            "— bytes decoded as GBK/Big5/Shift-JIS."
        )
    if stats["u8_bigrams"] >= 2:
        return (
            f'{stats["u8_bigrams"]} "Ð"+tail sequence(s) '
            "— UTF-8 Cyrillic decoded as Latin-1/CP1252."
        )
    if stats["cyrillic_chars"] == 0 and stats["accented_chars"] >= MIN_CP1251_CHARS:
        return (
            f'{stats["accented_chars"]} accented Latin-1 letters and no Cyrillic '
            "— CP1251 Cyrillic decoded as Latin-1."
        )
    return None


def run(client: InferHubClient, alias: str) -> dict:
    status, raw, ms = client.post(russian_payload(alias))
    if status != 200:
        return result(
            check_id="ru_mojibake",
            alias=alias,
            status="error",
            summary=http_preview(status, raw),
            http_status=status,
            latency_ms=ms,
        )
    chunks = parse_sse(raw)
    resolved = resolved_model(chunks, alias)
    answer = text_content(chunks)
    evidence: dict = mojibake_stats(answer)
    usage = last_usage(chunks)
    evidence["usage"] = {
        key: usage[key]
        for key in ("prompt_tokens", "completion_tokens")
        if isinstance(usage.get(key), int)
    }
    evidence["finish_reason"] = last_finish_reason(chunks)
    evidence["sample"] = answer[:120]
    if not chunks:
        return result(
            check_id="ru_mojibake",
            alias=alias,
            status="fail",
            summary="No SSE JSON chunks in the stream.",
            resolved_model=resolved,
            http_status=status,
            latency_ms=ms,
            evidence=evidence,
        )
    if not answer:
        return result(
            check_id="ru_mojibake",
            alias=alias,
            status="fail",
            summary="Stream contained no text content — nothing to score for mojibake.",
            resolved_model=resolved,
            http_status=status,
            latency_ms=ms,
            evidence=evidence,
        )
    verdict = mojibake_verdict(answer)
    if verdict:
        return result(
            check_id="ru_mojibake",
            alias=alias,
            status="fail",
            summary="Mojibake in the Russian answer: " + verdict,
            resolved_model=resolved,
            http_status=status,
            latency_ms=ms,
            evidence=evidence,
        )
    if evidence["cyrillic_chars"] == 0:
        return result(
            check_id="ru_mojibake",
            alias=alias,
            status="pass",
            summary=(
                "No mojibake, but the answer contained no Cyrillic — "
                "not recognizably Russian."
            ),
            resolved_model=resolved,
            http_status=status,
            latency_ms=ms,
            evidence=evidence,
        )
    return result(
        check_id="ru_mojibake",
        alias=alias,
        status="pass",
        summary=(
            f'Russian answer arrived clean — {evidence["chars"]} chars, '
            "no replacement characters, no double-encoded Cyrillic."
        ),
        resolved_model=resolved,
        http_status=status,
        latency_ms=ms,
        evidence=evidence,
    )
