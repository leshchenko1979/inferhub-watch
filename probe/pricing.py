"""Cost per M tokens per provider+model, from the Management API.

Two sources, both real billing rather than estimates:

- /usage/logs rows carry ask_input_per_mtok / ask_output_per_mtok — the
  per-M rates actually billed on each request — plus token counts and
  cost_consumer_usdc. Aggregated over 30d per model (the route string) they
  give the ask rates in use, the effective $/M over all tokens (cache
  discounts included) and the cache-hit share.
- /catalog carries each publisher's asks per model; used as the fallback
  for routes with no log rows yet.

Writes data/pricing.json for the site generator. Transient failures (an
HTTP 429 right after a probe run is the known one) are retried with backoff.
If every attempt fails the cron still survives: main() logs a warning,
emits a GitHub Actions annotation, exits 0, and leaves the previous file.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from probe.costs import MANAGEMENT, USER_AGENT, fetch_log_rows
from probe.registry import load_aliases, load_candidates, repo_root

CATALOG_TIMEOUT = 30
RANGE = "30d"
# 30d is ~5.7k rows; fetch_log_rows caps pages at 40 by default.
MAX_PAGES = 120


def _get(url: str, key: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(req, timeout=CATALOG_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def fetch_catalog(key: str) -> dict[str, tuple[float, float]]:
    """Map 'prefix/upstreamModelId' -> (cheapest askIn, cheapest askOut)."""
    body = _get(f"{MANAGEMENT}/catalog", key)
    entries = body if isinstance(body, list) else body.get("rows") or []
    asks: dict[str, tuple[float, float]] = {}
    for entry in entries:
        prefix = entry.get("prefix") or ""
        if not prefix or not entry.get("enabled"):
            continue
        for model in entry.get("models") or []:
            if not model.get("enabled") or model.get("modelDisabled"):
                continue
            name = model.get("upstreamModelId") or ""
            try:
                pair = (min(model.get("asksIn") or []), min(model.get("asksOut") or []))
            except (TypeError, ValueError):
                continue
            if name and all(v >= 0 for v in pair) and any(model.get("asksIn") or []):
                asks[f"{prefix}/{name}"] = pair
    return asks


def _float(raw: object) -> float | None:
    try:
        value = float(str(raw))
    except (TypeError, ValueError):
        return None
    return value


def aggregate_rows(rows: list[dict]) -> dict[str, dict]:
    """Per model string: traffic totals and the latest billed ask rates."""
    stats: dict[str, dict] = {}
    for row in sorted(rows, key=lambda r: r.get("ts") or ""):
        model = row.get("model") or ""
        if not model:
            continue
        agg = stats.setdefault(
            model,
            {
                "reqs": 0,
                "tok_in": 0,
                "tok_out": 0,
                "cached": 0,
                "cost": 0.0,
                "ask_in": None,
                "ask_out": None,
                "last_ts": "",
            },
        )
        agg["reqs"] += 1
        agg["tok_in"] += int(row.get("prompt_tokens") or 0)
        agg["tok_out"] += int(row.get("completion_tokens") or 0)
        agg["cached"] += int(row.get("cached_tokens") or 0)
        cost = _float(row.get("cost_consumer_usdc"))
        if cost is not None:
            agg["cost"] += cost
        ask_in = _float(row.get("ask_input_per_mtok"))
        ask_out = _float(row.get("ask_output_per_mtok"))
        if ask_in is not None and ask_out is not None:
            agg["ask_in"], agg["ask_out"] = ask_in, ask_out
        agg["last_ts"] = row.get("ts") or agg["last_ts"]
    return stats


def daily_series(rows: list[dict]) -> list[dict]:
    """Per-UTC-day spend and request totals over the fetched rows, oldest first."""
    per_day: dict[str, dict] = {}
    for row in rows:
        day = (row.get("ts") or "")[:10]
        if not day:
            continue
        agg = per_day.setdefault(day, {"cost": 0.0, "requests": 0})
        agg["requests"] += 1
        cost = _float(row.get("cost_consumer_usdc"))
        if cost is not None:
            agg["cost"] += cost
    return [
        {
            "date": day,
            "cost_usdc": f"{per_day[day]['cost']:.6f}",
            "requests": per_day[day]["requests"],
        }
        for day in sorted(per_day)
    ]


def route_entry(stats: dict | None, catalog: dict, alias: str, *, candidate: bool = False) -> dict:
    """One route as it lands in pricing.json — logs first, catalog fallback."""
    if not stats:
        ask_in, ask_out = catalog.get(alias) or (None, None)
        entry = {
            "ask_in": ask_in,
            "ask_out": ask_out,
            "eff_per_mtok": None,
            "cache_pct": None,
            "reqs": 0,
            "tok_in": 0,
            "tok_out": 0,
            "cost_usdc": None,
            "last_ts": None,
            "source": "catalog" if ask_in is not None else "none",
        }
    else:
        toks = stats["tok_in"] + stats["tok_out"]
        eff = stats["cost"] / toks * 1e6 if toks else None
        cache_pct = stats["cached"] / stats["tok_in"] * 100 if stats["tok_in"] else None
        entry = {
            "ask_in": stats["ask_in"],
            "ask_out": stats["ask_out"],
            "eff_per_mtok": round(eff, 4) if eff is not None else None,
            "cache_pct": round(cache_pct, 1) if cache_pct is not None else None,
            "reqs": stats["reqs"],
            "tok_in": stats["tok_in"],
            "tok_out": stats["tok_out"],
            "cost_usdc": f'{stats["cost"]:.6f}',
            "last_ts": stats["last_ts"],
            "source": "usage-logs",
        }
    if candidate:
        entry["candidate"] = True
    return entry


def snapshot(key: str, aliases: list[str], range_: str = RANGE,
             candidates: list[str] | None = None) -> dict:
    """Build the full pricing payload; candidate routes are flagged as such."""
    rows = fetch_log_rows(key, range_=range_, max_pages=MAX_PAGES)
    stats = aggregate_rows(rows)
    catalog = fetch_catalog(key)
    cand = set(candidates or [])
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "range": range_,
        "requests_scanned": len(rows),
        "days": daily_series(rows),
        "routes": {
            alias: route_entry(stats.get(alias), catalog, alias, candidate=alias in cand)
            for alias in aliases
        },
    }


def snapshot_routes() -> tuple[list[str], list[str]]:
    """(all routes for the snapshot, the candidate subset) — board first, deduped."""
    routes = list(load_aliases())
    cand: list[str] = []
    for group in load_candidates():
        for route in group["routes"]:
            if route not in routes and route not in cand:
                cand.append(route)
    return routes + cand, cand


def write_outputs(payload: dict, root: Path | None = None) -> tuple[Path, Path]:
    """Write data/pricing.json plus the dated copy data/pricing/YYYY-MM-DD.json.

    Same-day re-runs overwrite that day's copy; history accumulates per day.
    """
    root = root or repo_root()
    text = json.dumps(payload, indent=2) + "\n"
    latest = root / "data" / "pricing.json"
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text(text)
    dated = root / "data" / "pricing" / f"{datetime.now(timezone.utc):%Y-%m-%d}.json"
    dated.parent.mkdir(parents=True, exist_ok=True)
    dated.write_text(text)
    return latest, dated


# A probe run burns rate budget; the pricing fetch right after it is the
# classic 429 window. Back off and retry before giving up.
ATTEMPTS = 3
RETRY_BACKOFF_S = 30


def main() -> int:  # noqa: BLE001 — pricing must never break the cron
    key = os.environ.get("INFERHUB_API_KEY", "").strip()
    if not key:
        print("INFERHUB_API_KEY is required", file=sys.stderr)
        return 0
    try:
        routes, cand = snapshot_routes()
    except Exception as exc:  # noqa: BLE001
        print(f"warning: could not load routes: {exc}", file=sys.stderr)
        return 0
    last_exc: Exception | None = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            payload = snapshot(key, routes, candidates=cand)
            latest, dated = write_outputs(payload)
            print(latest)
            print(dated)
            return 0
        except Exception as exc:  # noqa: BLE001 — e.g. HTTP Error 429
            last_exc = exc
            if attempt < ATTEMPTS:
                wait = RETRY_BACKOFF_S * attempt
                print(
                    f"warning: pricing attempt {attempt}/{ATTEMPTS} failed "
                    f"({exc}); retrying in {wait}s",
                    file=sys.stderr,
                )
                time.sleep(wait)
    print(
        f"warning: pricing snapshot failed after {ATTEMPTS} attempts, "
        f"keeping previous file: {last_exc}",
        file=sys.stderr,
    )
    # Surface the staleness in CI — silent success is how this went unnoticed.
    print(f"::warning::pricing snapshot failed ({last_exc}); data/pricing.json is STALE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
