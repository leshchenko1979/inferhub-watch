"""Core probe: one request that asserts stream shape, tool call, and a clean
Russian answer in one trip. The answer is expected on the tool argument
(`report_answer.answer`) — under `tool_choice: "required"` several routes emit
zero text content. Every failing sub-assertion is named in the summary.

Stream-shape standard = what the consuming runtime's tool-call accumulator
actually survives, not the textbook OpenAI spec (see
`oc-work/stream-quirk-risk-2026-08-28.md`): empty-string tool names are
tolerated (the consumer skips them, first non-empty sticks), while an
empty-string `finish_reason` is terminal to the consumer and flushes the
tool call mid-stream — so only the latter fails this check."""

from __future__ import annotations

from probe.http import InferHubClient
from probe.mojibake import (
    mojibake_stats,
    mojibake_verdict,
    text_content,
    tool_argument_text,
)
from probe.payloads import core_payload
from probe.result import http_preview, result
from probe.sse import (
    inspect_stream,
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
            check_id="core",
            alias=alias,
            status="error",
            summary=http_preview(status, raw),
            http_status=status,
            latency_ms=ms,
        )
    chunks = parse_sse(raw)
    resolved = resolved_model(chunks, alias)
    stats = inspect_stream(chunks)
    usage = last_usage(chunks)
    content = text_content(chunks)
    argument = tool_argument_text(chunks)
    answer = content + argument
    evidence: dict = dict(stats)
    evidence["usage"] = usage_pricing_fields(usage)
    evidence.update(mojibake_stats(answer))
    evidence["content_chars"] = len(content)
    evidence["argument_chars"] = len(argument)
    evidence["finish_reason"] = stats["last_finish_reason"]
    evidence["sample"] = answer[:120]

    if not chunks:
        return result(
            check_id="core",
            alias=alias,
            status="fail",
            summary="No SSE JSON chunks in the stream.",
            resolved_model=resolved,
            http_status=status,
            latency_ms=ms,
            evidence=evidence,
        )
    if stats["empty_finish_chunks"]:
        # Empty-string finish_reason is TERMINAL to the consuming runtime's
        # accumulator: it flushes the tool call mid-stream (json_repair("")
        # -> {}), then the drained map re-accumulates the argument tail and
        # flushes again — empty/duplicated tool runs. Empty-string tool NAMES
        # are tolerated (the consumer skips them; the first non-empty name
        # sticks), so they are kept in evidence only and never fail the check.
        return result(
            check_id="core",
            alias=alias,
            status="fail",
            summary=(
                f'{stats["empty_finish_chunks"]} event(s) set finish_reason to "" — '
                "the consumer treats that as terminal and flushes tool calls "
                "mid-stream (empty/duplicated runs)."
            ),
            resolved_model=resolved,
            http_status=status,
            latency_ms=ms,
            evidence=evidence,
        )
    if not stats["names"]:
        return result(
            check_id="core",
            alias=alias,
            status="fail",
            summary="Stream ended without a non-empty tool name.",
            resolved_model=resolved,
            http_status=status,
            latency_ms=ms,
            evidence=evidence,
        )
    if not answer:
        return result(
            check_id="core",
            alias=alias,
            status="fail",
            summary="Ordered Russian text, got none — no content and no answer argument.",
            resolved_model=resolved,
            http_status=status,
            latency_ms=ms,
            evidence=evidence,
        )
    verdict = mojibake_verdict(answer)
    if verdict:
        return result(
            check_id="core",
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
            check_id="core",
            alias=alias,
            status="fail",
            summary="No Cyrillic in the answer — the prompt orders Russian text.",
            resolved_model=resolved,
            http_status=status,
            latency_ms=ms,
            evidence=evidence,
        )
    where = "in tool arguments" if argument and not content else "as text content"
    return result(
        check_id="core",
        alias=alias,
        status="pass",
        summary=(
            f'Streamed report_answer with a clean Russian answer — '
            f'{evidence["chars"]} chars, {evidence["cyrillic_chars"]} Cyrillic, '
            f"{where}."
        ),
        resolved_model=resolved,
        http_status=status,
        latency_ms=ms,
        evidence=evidence,
    )