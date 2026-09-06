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
from probe.pricing import _float, bump_usage, failure_stats, rate_label
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
        bump_usage(agg, row)
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
            if was_v:  # normal reprice
                rel = (now_v - was_v) / was_v * 100
                if abs(rel) < MOVE_PCT:
                    continue
            else:
                # was 0 -> now priced (or priced -> 0): a real move at any
                # size (review A3; rel would be 0.0 and the old gate hid it
                # forever). pct=None renders as (new price).
                rel = None
            moves.append(
                {"alias": alias, "field": field,
                 "was": float(was_v), "now": float(now_v), "pct": rel}
            )
    return moves


def _fmt_pct(pct):
    if pct is None:
        return "(new price)"
    return f"({pct:+.1f}%)"


def _ask(v: float | None) -> str:
    """Money label without the $ prefix (report-table house style)."""
    if v is None:
        return "—"
    return rate_label(v, prefix="") or "—"


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


# Boarding-candidate gate (owner review 2026-09-06): a non-board route
# earns a flag for one-word owner approval when ALL FOUR hold.
BOARD_SNAPSHOTS = 3      # consecutive daily snapshots under the bar
BOARD_TTFT_MULTIPLE = 2.0

def boarding_candidates(routes_by_day: list[dict], run: dict | None,
                        perf: dict, aliases: list[str]) -> list[dict]:
    """[{route, eff, bar, iq, why}] — non-board routes meeting all four gates.

    (a) core pass on the newest run covering the route; (b) realized eff
    $/M under the family incumbent bar in the last BOARD_SNAPSHOTS daily
    snapshots; (c) IQ wash vs the family incumbent (auto-derived AA
    slugs); (d) ttft p50 not more than BOARD_TTFT_MULTIPLE x the
    incumbent's (perf = {route: {ttft_p50_ms}}). Gates with missing data
    degrade: no probe record fails (a), no IQ or ttft data passes (c)/(d)
    — a route must EARN the flag on what is known.
    """
    from probe.market import _route_iq, family, incumbent_bar

    board = set(aliases)
    fams: dict[str, list[str]] = {}
    for alias in aliases:
        fams.setdefault(family(alias), []).append(alias)

    def _ttft(route: str) -> float | None:
        ttft = (perf.get(route) or {}).get("ttft_p50_ms")
        return float(ttft) if ttft is not None else None

    out: list[dict] = []
    routes = routes_by_day[-1] if routes_by_day else {}
    for route, entry in sorted((routes or {}).items()):
        if not isinstance(entry, dict) or route in board:
            continue
        eff = entry.get("eff_per_mtok")
        if eff is None or not entry.get("reqs"):
            continue  # never billed: nothing to board on
        fam = family(route)
        incumbents = fams.get(fam) or []
        if not incumbents:
            continue
        # (a) core pass on the newest run covering the route
        cells = [c for c in ((run or {}).get("cells") or [])
                 if c.get("alias") == route]
        core = [c for c in cells if c.get("check_id") == "core"]
        if not core or core[-1].get("status") != "pass":
            continue
        # (b) eff under the incumbent bar in the last N snapshots
        bars: list[float] = []
        under_days = 0
        for day_routes in routes_by_day[-BOARD_SNAPSHOTS:]:
            day_eff = (day_routes.get(route) or {}).get("eff_per_mtok")
            bar, _ = incumbent_bar(day_routes, incumbents)
            if bar is not None:
                bars.append(bar)
            if day_eff is not None and bar is not None and day_eff < bar:
                under_days += 1
        if under_days < BOARD_SNAPSHOTS or not bars:
            continue
        bar = min(bars)
        # (c) quality wash — unknown IQ passes
        inc_iq = max((iq for r in incumbents if (iq := _route_iq(r)) is not None),
                     default=None)
        cand_iq = _route_iq(route)
        if inc_iq is not None and cand_iq is not None and cand_iq < inc_iq * 0.9:
            continue
        # (d) speed — unknown ttft passes
        inc_ttft = min((t for r in incumbents if (t := _ttft(r)) is not None),
                       default=None)
        cand_ttft = _ttft(route)
        if (inc_ttft is not None and cand_ttft is not None
                and cand_ttft > inc_ttft * BOARD_TTFT_MULTIPLE):
            continue
        out.append({"route": route, "eff": eff, "bar": bar,
                    "iq": cand_iq, "inc_iq": inc_iq})
    return out

def boarding_section(candidates: list[dict]) -> list[str]:
    if not candidates:
        return []
    lines = ["", "**Boarding candidates** — met all four gates; one word boards it:"]
    for c in candidates:
        iq = f", iq {c['iq']}" if c.get("iq") is not None else ""
        lines.append(
            f"- `{c['route']}`: eff {_ask(c['eff'])} vs bar {_ask(c['bar'])}{iq}"
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
            f"{_ask(m['was'])} → {_ask(m['now'])} {_fmt_pct(m['pct'])}"
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
    lines += boarding_section(boarding_data(root))
    return "\n".join(lines).strip() + "\n"

def boarding_data(root: Path) -> list[dict]:
    """Assemble the four-gate inputs from on-disk artifacts."""
    from probe.market import _perf_block
    from probe.radar import latest_run
    from probe.registry import load_aliases

    snaps = _latest_snapshots(root, n=BOARD_SNAPSHOTS)
    return boarding_candidates(
        [payload.get("routes") or {} for _, payload in snaps],
        latest_run(root),
        _perf_block(),
        load_aliases(),
    )


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
