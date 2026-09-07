"""Data-freshness gate and chip (P0b): stale data must be loud, not silent."""
from __future__ import annotations

from datetime import datetime, timezone

# The nightly sweep (02:00Z cron, Actions drift up to ~4h) means committed
# data should never be older than ~30h when the pages job runs. Beyond that
# the sweep is failing and the page would publish stale numbers silently.
STALE_HOURS = 30


def parse_generated_at(payload: dict | None) -> datetime | None:
    """pricing.json generated_at as an aware datetime (None if missing)."""
    raw = str((payload or {}).get("generated_at") or "")
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def freshness(payload: dict | None, *, now: datetime | None = None) -> dict:
    """{age_hours, stale, label} for the pricing payload."""
    ts = parse_generated_at(payload)
    now = now or datetime.now(timezone.utc)
    if ts is None:
        return {"age_hours": None, "stale": True, "label": "age unknown"}
    age_h = (now - ts).total_seconds() / 3600.0
    return {
        "age_hours": round(age_h, 1),
        "stale": age_h > STALE_HOURS,
        "label": _label(age_h),
    }


def _label(age_h: float) -> str:
    if age_h < 1.0:
        return "fresh (<1h)"
    if age_h < 24.0:
        return f"{age_h:.0f}h old"
    return f"{age_h / 24.0:.1f}d old"


def chip_html(payload: dict | None, *, now: datetime | None = None) -> str:
    """Header chip: green when fresh, red when stale."""
    info = freshness(payload, now=now)
    cls = "stale" if info["stale"] else "fresh"
    return (
        f'<span class="freshness-chip {cls}" '
        f'title="pricing.json age; stale beyond {STALE_HOURS}h">'
        f"&#9679; data {info['label']}</span>"
    )


def gate(payload: dict | None, *, now: datetime | None = None,
         fail_stale: bool = False) -> int:
    """Exit code for CI freshness gates. 0 unless fail_stale and stale."""
    info = freshness(payload, now=now)
    if info["stale"]:
        age = info["age_hours"]
        msg = f"stale pricing data ({age}h > {STALE_HOURS}h); sweep is failing"
        if fail_stale:
            print(f"::error::{msg}", file=__import__("sys").stderr)
            return 1
        print(f"::warning::{msg}", file=__import__("sys").stderr)
    return 0
