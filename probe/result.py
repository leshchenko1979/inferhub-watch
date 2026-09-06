from __future__ import annotations

from typing import Any

def http_preview(status: int, raw: str, limit: int = 180) -> str:
    return f"HTTP {status}: {raw[:limit].replace(chr(10), ' ')}"

def result(
    *,
    check_id: str,
    alias: str,
    status: str,
    summary: str,
    resolved_model: str = "",
    http_status: int | None = None,
    latency_ms: float | None = None,
    ttft_ms: float | None = None,
    tps: float | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "alias": alias,
        "resolved_model": resolved_model,
        "status": status,
        "summary": summary,
        "http_status": http_status,
        "latency_ms": round(latency_ms, 1) if latency_ms is not None else None,
        "ttft_ms": round(ttft_ms, 1) if ttft_ms is not None else None,
        "tps": round(tps, 2) if tps is not None else None,
        "evidence": evidence or {},
    }
