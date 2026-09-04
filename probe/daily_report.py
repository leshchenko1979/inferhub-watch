"""Daily usage/spend/price-movement report — free Management API reads.

Prints a Telegram-ready markdown report to stdout:

- last 24h usage per route from the live /usage/logs read (free — no
  probe spend, ever), with failed-request counts and codes;
- spend by UTC day from the newest dated pricing snapshot;
- ask-rate movements between the two newest dated snapshots (the same
  median asks the board and the projection basis use). Median asks are
  recomputed daily under identical window semantics, so a real move
  shows up; sub-0.5% deltas are float noise and dropped.

The key comes from $INFERHUB_API_KEY or the OpenCrabs keys file and is
never printed. Exit 0 only when the report was produced.
"""

from __future__ import annotations

import json
import os
import sys
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from probe.costs import fetch_log_rows
from probe.pricing import _float, failure_stats
from probe.registry import repo_root

KEYS_FILE = Path.home() / ".opencrabs" / "profiles" / "ops" / "keys.toml"
KEY_PATH = ("providers", "custom", "inferhub", "api_key")
# Median-ask recomputation is deterministic, so identical inputs give
# identical values; anything under this relative % is float noise.
MOVE_PCT = 0.5
MSK = timezone(timedelta(hours=3))  # the operator's wall clock


def load_key() -> str:
    """INFERHUB_API_KEY from the env, else the OpenCrabs keys file."""
    key = os.environ.get("INFERHUB_API_KEY", "").strip()
    if key:
        return key
    try:
        data = tomllib.loads(KEYS_FILE.read_text())
        for part in KEY_PATH:
            data = data[part]
        return str(data).strip()
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError):
        return ""


def usage_24h(rows: list[dict]) -> dict[str, dict]:
    """Per model over the fetched window: traffic, cache share, spend.

    Failed rows carry no tokens and zero cost (see probe.pricing), so
    they are counted as requests and failures only.
    """
    out: dict[str, dict] = {}
    for row in rows:
        model = row.get("model") or ""
        if not model:
            continue
        agg = out.setdefault(
            model,
            {"reqs": 0, "failed": 0, "tok_in": 0, "tok_out": 0,
             "cached": 0, "cost": 0.0},
        )
        agg["reqs"] += 1
        if row.get("status") != "ok":
            agg["failed"] += 1
            continue
        agg["tok_in"] += int(row.get("prompt_tokens") or 0)
        agg["tok_out"] += int(row.get("completion_tokens") or 0)
        agg["cached"] += int(row.get("cached_tokens") or 0)
        cost = _float(row.get("cost_consumer_usdc"))
        if cost is not None:
            agg["cost"] += cost
    return out


def _latest_snapshots(root: Path, n: int = 2) -> list[tuple[str, dict]]:
    """The n newest dated snapshots as (date, payload), oldest first."""
    out: list[tuple[str, dict]] = []
    for path in sorted((root / "data" / "pricing").glob("*.json"))[-n:]:
        try:
            out.append((path.stem, json.loads(path.read_text())))
        except (OSError, ValueError):
            continue
    return out


def price_movements(root: Path | None = None) -> list[dict]:
    """Ask changes between the two newest dated snapshots."""
    root = root or repo_root()
    snaps = _latest_snapshots(root)
    if len(snaps) < 2:
        return []
    (_, old), (new_day, new) = snaps
    moves: list[dict] = []
    for alias in sorted(new.get("routes") or {}):
        prev = (old.get("routes") or {}).get(alias) or {}
        for field in ("ask_in", "ask_out"):
            now_v = (new["routes"][alias] or {}).get(field)
            was_v = (prev or {}).get(field)
            if now_v is None or was_v is None or abs(now_v - was_v) <= 1e-9:
                continue
            rel = (now_v - was_v) / was_v * 100 if was_v else 0.0
            if abs(rel) < MOVE_PCT:
                continue
            moves.append(
                {"alias": alias, "field": field,
                 "was": float(was_v), "now": float(now_v), "pct": rel}
            )
    return moves


def _ask(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:,.2f}" if abs(v) >= 1 else f"{v:.4f}".rstrip("0").rstrip(".")


def usage_section(stats: dict[str, dict], rows: list[dict]) -> list[str]:
    total_cost = sum(s["cost"] for s in stats.values())
    total_reqs = sum(s["reqs"] for s in stats.values())
    total_failed = sum(s["failed"] for s in stats.values())
    lines = [
        f"**Last 24h** — {total_reqs} reqs · {_ask(total_cost)} USDC "
        f"· {total_failed} failed",
    ]
    if not stats:
        return lines + ["No traffic in the window."]
    if total_failed:
        fs = failure_stats(rows)
        codes = fs.get("codes") or {}
        pretty = ", ".join(f"{code} ×{n}" for code, n in list(codes.items())[:3])
        if pretty:
            lines.append(f"Failed codes: {pretty}")
    lines += [
        "",
        "| route | reqs | tok in | tok out | cache | spend |",
        "|---|---|---|---|---|---|",
    ]
    for model in sorted(stats, key=lambda m: -stats[m]["cost"]):
        s = stats[model]
        cache = f"{s['cached'] / s['tok_in'] * 100:.0f}%" if s["tok_in"] else "—"
        lines.append(
            f"| {model} | {s['reqs']} | {s['tok_in']:,} | {s['tok_out']:,} "
            f"| {cache} | {_ask(s['cost'])} |"
        )
    return lines


def spend_by_day_section(root: Path) -> list[str]:
    snaps = _latest_snapshots(root, n=1)
    if not snaps:
        return []
    days = snaps[0][1].get("days") or []
    if not days:
        return []
    lines = [
        "",
        "**Spend by UTC day** (as of the latest snapshot)",
        "",
        "| day | reqs | spend |",
        "|---|---|---|",
    ]
    for day in days[-3:]:
        lines.append(
            f"| {day.get('date', '?')} | {day.get('requests', 0):,} "
            f"| {_ask(_float(day.get('cost_usdc')) or 0.0)} |"
        )
    return lines


def movements_section(moves: list[dict], root: Path) -> list[str]:
    snaps = _latest_snapshots(root, n=2)
    span = f"{snaps[0][0]} → {snaps[1][0]}" if len(snaps) == 2 else ""
    lines = ["", f"**Ask moves** ({span})" if span else "**Ask moves**"]
    if not moves:
        lines.append("No ask moves — board prices quiet.")
        return lines
    grouped: dict[str, list[dict]] = {}
    for mv in moves:
        grouped.setdefault(mv["alias"], []).append(mv)
    for alias, items in grouped.items():
        parts = [
            f"{'in' if m['field'] == 'ask_in' else 'out'} "
            f"{_ask(m['was'])} → {_ask(m['now'])} ({m['pct']:+.1f}%)"
            for m in items
        ]
        lines.append(f"- `{alias}`: " + " · ".join(parts))
    return lines


def build_report(key: str, root: Path | None = None) -> str:
    root = root or repo_root()
    now = datetime.now(MSK)
    rows = fetch_log_rows(key, range_="24h", pace_s=0.25)
    stats = usage_24h(rows)
    lines = [f"📊 **Inferhub daily** — {now:%Y-%m-%d %H:%M} MSK", ""]
    lines += usage_section(stats, rows)
    lines += spend_by_day_section(root)
    lines += movements_section(price_movements(root), root)
    return "\n".join(lines).strip() + "\n"


def main() -> int:
    key = load_key()
    if not key:
        print(
            "no INFERHUB_API_KEY in env and none in keys file",
            file=sys.stderr,
        )
        return 1
    try:
        report = build_report(key)
    except Exception as exc:  # noqa: BLE001 — the cron reports the failure
        print(f"daily report failed: {exc}", file=sys.stderr)
        return 1
    print(report, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
