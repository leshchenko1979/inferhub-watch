"""Core probe: one request that asserts stream shape, tool call, and a clean
Russian answer in one trip. The answer is expected on the tool argument
(`report_answer.answer`) — under `tool_choice: "required"` several routes emit
zero text content. Every failing sub-assertion is named in the summary.

Stream-shape standard = what the consuming runtime's tool-call accumulator
actually survives, not the textbook [OI] spec (see
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


def stream_perf(
    ttft_ms: float | None, elapsed_ms: float, usage: dict
) -> tuple[float | None, float | None]:
    """(ttft_ms, tps) from the client's TTFT sample, total elapsed, and usage.

    TPS = completion_tokens / stream seconds, where stream seconds span
    first-line -> end-of-body: generation happens strictly between the two,
    so the denominator excludes connect + time-to-first-token. None when
    either side is missing (error path, no usage) or degenerate
    (elapsed <= ttft).
    """
    if ttft_ms is None:
        return None, None
    ttft = float(ttft_ms)
    stream_s = (float(elapsed_ms) - ttft) / 1000.0
    out = usage.get("completion_tokens")
    if not isinstance(out, int) or out <= 0 or stream_s <= 0:
        return ttft, None
    return ttft, out / stream_s

def run(client: InferHubClient, alias: str) -> dict:
    payload = core_payload(alias)
    status, raw, ms, ttft = client.post(payload)
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
    ttft_ms, tps = stream_perf(ttft, ms, usage)
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
            ttft_ms=ttft_ms,
            tps=tps,
            evidence=evidence,
        )
    if stats["empty_finish_chunks"] and stats["last_finish_reason"] in ("", None):
        # Owner recalibration 2026-09-07 ("cbcn/glm-5.3-flash works fine in
        # reality"): empty-string finish_reason on INTERMEDIATE chunks is
        # tolerated evidence, not a failure — the route streams clean answers
        # and terminates properly. What matters to a consumer is the ENDING:
        # a stream whose last finish_reason is "" (or never carries a real
        # terminal reason) is the broken case. Mirrors the empty-tool-name
        # tolerance above; the counts stay in evidence.
        return result(
                check_id="core",
                alias=alias,
                status="fail",
                summary=(
                    f'{stats["empty_finish_chunks"]} event(s) set finish_reason to "" and the '
                    "stream never terminates with a real reason — the consumer cannot tell "
                    "where the tool call ends."
                ),
                resolved_model=resolved,
                http_status=status,
                latency_ms=ms,
                ttft_ms=ttft_ms,
                tps=tps,
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
            ttft_ms=ttft_ms,
            tps=tps,
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
            ttft_ms=ttft_ms,
            tps=tps,
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
            ttft_ms=ttft_ms,
            tps=tps,
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
            ttft_ms=ttft_ms,
            tps=tps,
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
        ttft_ms=ttft_ms,
        tps=tps,
        evidence=evidence,
    )
