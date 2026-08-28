"""Mojibake scoring for probe answers, shared by check modules.

Extracted from the retired checks/ru_mojibake check. Under
`tool_choice: "required"` several routes emit the answer only in the tool
argument, so callers score `text_content(chunks) + tool_argument_text(chunks)`.
"""

from __future__ import annotations

import json
import re

from probe.sse import tool_deltas

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


def tool_argument_text(chunks: list[dict]) -> str:
    """The `answer` field of the report_answer call assembled from streamed fragments.

    Tool arguments stream as pieces of JSON (often split mid-string across
    chunks). Fragments are concatenated per index, JSON-parsed once, and the
    `answer` field returned. Returns "" when there is no parseable answer —
    callers treat that as "no Russian text".
    """
    fragments: dict[int, str] = {}
    order: list[int] = []
    for chunk in chunks:
        for tc in tool_deltas(chunk):
            func = tc.get("function") or {}
            index = int(tc.get("index") or 0)
            if index not in fragments:
                order.append(index)
            fragments[index] = fragments.get(index, "") + (func.get("arguments") or "")
    assembled = "".join(fragments[i] for i in order).strip()
    if not assembled:
        return ""
    try:
        data = json.loads(assembled)
    except (TypeError, ValueError):
        return ""
    if isinstance(data, dict) and isinstance(data.get("answer"), str):
        return data["answer"]
    return ""


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