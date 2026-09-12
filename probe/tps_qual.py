"""Candidate Sustained TPS Qualification Probe (Issue #22).

Runs targeted deep streaming probes (~500+ output tokens, stream duration >= 2.0s)
for candidate models to measure empirical sustained throughput (TPS) without burst
or buffering artifacts.

Subtracts pre-TTFT reasoning tokens so thinking models are accurately scored on
visible stream delivery rate. Persists results to `data/qual_runs.json`.
"""

from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path
from typing import Any

from probe import sse

logger = logging.getLogger("tps_qual")

MIN_STREAM_DUR_S = 2.0
MIN_VALID_TPS = 1.0
MAX_VALID_TPS = 500.0
DEFAULT_TARGET_TOKENS = 600

SUSTAINED_PROMPT = (
    "Explain the Raft distributed consensus algorithm in technical detail. "
    "Cover leader election, log replication, safety invariants, and joint consensus "
    "membership changes step by step with clear explanations. Aim for 400 to 600 words."
)


def build_qual_payload(model: str, max_tokens: int = 1000) -> dict[str, Any]:
    """Build a streaming payload intended to produce 400-600 output tokens."""
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a precise technical explainer."},
            {"role": "user", "content": SUSTAINED_PROMPT},
        ],
        "stream": True,
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }


def parse_stream_metrics(
    raw: str,
    elapsed_ms: float,
    ttft_ms: float | None,
) -> dict[str, Any]:
    """Parse SSE stream output to extract token counts, duration, and TPS.

    Deducts pre-TTFT reasoning tokens if reported in completion_tokens_details.
    """
    chunks = sse.parse_sse(raw)
    usage: dict[str, Any] | None = None
    accumulated_text: list[str] = []

    for chunk in chunks:
        if "usage" in chunk and chunk["usage"]:
            usage = chunk["usage"]
        choices = chunk.get("choices") or []
        if choices:
            choice = choices[0]
            delta = choice.get("delta") or {}
            content = delta.get("content")
            if content:
                accumulated_text.append(content)

    full_text = "".join(accumulated_text)

    # Resolve completion tokens and reasoning tokens
    completion_tokens = 0
    reasoning_tokens = 0
    if usage:
        completion_tokens = int(usage.get("completion_tokens") or 0)
        details = usage.get("completion_tokens_details") or {}
        reasoning_tokens = int(details.get("reasoning_tokens") or 0)

    # Fallback if usage is not emitted in SSE stream
    if completion_tokens <= 0 and full_text:
        # Approximate ~1.3 tokens per word for English technical text
        words = len(full_text.split())
        completion_tokens = max(1, int(words * 1.3))

    visible_tokens = max(0, completion_tokens - reasoning_tokens)

    # Stream duration calculation
    if ttft_ms is not None and elapsed_ms > ttft_ms:
        stream_dur_s = (elapsed_ms - ttft_ms) / 1000.0
    else:
        stream_dur_s = elapsed_ms / 1000.0

    tps: float | None = None
    valid = False
    if stream_dur_s >= MIN_STREAM_DUR_S and visible_tokens > 0:
        raw_tps = visible_tokens / stream_dur_s
        if MIN_VALID_TPS <= raw_tps <= MAX_VALID_TPS:
            tps = round(raw_tps, 1)
            valid = True

    return {
        "valid": valid,
        "tps": tps,
        "completion_tokens": completion_tokens,
        "reasoning_tokens": reasoning_tokens,
        "visible_tokens": visible_tokens,
        "elapsed_ms": elapsed_ms,
        "ttft_ms": ttft_ms,
        "stream_dur_s": round(stream_dur_s, 3),
        "text_len": len(full_text),
    }


def load_qual_runs(qual_path: Path) -> dict[str, Any]:
    """Load qual_runs.json data."""
    if not qual_path.exists():
        return {"models": {}}
    try:
        return json.loads(qual_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning(f"Failed to load qual runs from {qual_path}: {exc}")
        return {"models": {}}


def save_qual_runs(data: dict[str, Any], qual_path: Path) -> None:
    """Save qual_runs.json atomically."""
    qual_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = qual_path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temp_path.replace(qual_path)


def record_qualification_result(
    model: str,
    metrics: dict[str, Any],
    qual_path: Path,
) -> dict[str, Any]:
    """Record a valid qualification result into qual_runs.json."""
    data = load_qual_runs(qual_path)
    models = data.setdefault("models", {})
    existing = models.get(model, {})

    entry = {
        "tps": metrics.get("tps"),
        "visible_tokens": metrics.get("visible_tokens"),
        "reasoning_tokens": metrics.get("reasoning_tokens"),
        "stream_dur_s": metrics.get("stream_dur_s"),
        "n": existing.get("n", 0) + 1,
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    models[model] = entry
    save_qual_runs(data, qual_path)
    return entry


def run_candidate_tps_qual(
    client: Any,
    model: str,
    qual_path: Path | None = None,
) -> dict[str, Any]:
    """Execute a sustained candidate TPS qualification probe and persist if valid."""
    payload = build_qual_payload(model)
    status, raw, elapsed_ms, ttft_ms = client.post(payload)

    if status != 200:
        return {
            "status": status,
            "valid": False,
            "error": f"HTTP {status}",
            "tps": None,
        }

    metrics = parse_stream_metrics(raw, elapsed_ms, ttft_ms)
    if metrics["valid"] and qual_path is not None:
        record_qualification_result(model, metrics, qual_path)

    return metrics
