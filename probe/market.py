"""Market radar — the catalog picks the candidates.

The daily candidate sweep no longer follows a hand-maintained list. Each
run reads the live /catalog asks, predicts each route's billed $/M for
every board family, and probes only routes predicted cheaper than the
cheapest in-use route of that family. Proven routes are cached in
data/proven.json and are not re-probed within 7 days (absolute TTL — a
repricing does NOT lift it).

predicted $/M = ask_in * (1 - cache_rate) * w_in + ask_out * w_out

with the family cache rate and the input/output token mix taken from the
cheapest incumbent's entry in data/pricing.json (fallback w_in 0.75,
cache_rate 0 when unknown). The incumbent bar is the cheapest BILLED
effective $/M among the family's board aliases, so raw-ask traps (a high
ask that bills cheap thanks to caching) cannot fire.

Parking law (owner rule 2026-09-04): the 7-day freeze is earned only by
a conclusively PASSING probe. A parked FAIL re-enters the shortlist on
the next sweep whenever its worst-case (zero-cache) predicted $/M still
beats the bar — a cache problem must not bury a genuinely cheaper ask.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from probe.pricing import fetch_catalog
from probe.registry import load_aliases, repo_root

PROVEN_TTL = timedelta(days=7)
TOP_N = 2
FALLBACK_W_IN = 0.75


# Dated snapshots of a board model rank inside the family whose price bar
# they compete against: deepseek-v4-flash-0731 vs deepseek-v4-flash,
# deepseek-v4-pro-0813 vs deepseek-v4-pro. The board itself may carry the
# dated tail (ali/deepseek-v4-flash-0731) — the map keeps the pinned
# snapshot inside its family. glm-5.3-flash is deliberately NOT mapped:
# the owner runs it as its own family, apart from glm-5.3. A tail absent
# from the map keeps its own family.
FAMILY_ALIASES = {
    "deepseek-v4-flash-0731": "deepseek-v4-flash",
    "deepseek-v4-pro-0813": "deepseek-v4-pro",
}


def family(route: str) -> str:
    """Model family = last path segment, alias-normalized to the board
    family (`cp/cline-pass/x` -> `x`;
    `ali/deepseek-v4-flash-0731` -> `deepseek-v4-flash`)."""
    tail = route.rsplit("/", 1)[-1]
    return FAMILY_ALIASES.get(tail, tail)


# Owner directive (2026-08-29): dated-only candidacy. Families that have a
# dated snapshot admit only dated snapshots as candidates — a plain tail is
# a different upstream model, not a cheaper substitute.
DATED_FAMILIES = frozenset(FAMILY_ALIASES.values())


def dated_eligible(route: str) -> bool:
    """True when the route may stand as a candidate for its family.

    Undated tails of a dated family (plain `deepseek-v4-flash` /
    `deepseek-v4-pro` under any publisher) are ineligible; the dated
    snapshots themselves and families without a dated alias pass.
    """
    if family(route) not in DATED_FAMILIES:
        return True
    return route.rsplit("/", 1)[-1] in FAMILY_ALIASES


def load_pricing(root: Path | None = None) -> dict | None:
    """data/pricing.json payload, or None when absent/unreadable."""
    root = root or repo_root()
    try:
        return json.loads((root / "data" / "pricing.json").read_text())
    except (OSError, ValueError):
        return None


def load_proven(root: Path | None = None) -> dict:
    """data/proven.json {route: {last_probe, statuses}}; {} when absent."""
    root = root or repo_root()
    try:
        data = json.loads((root / "data" / "proven.json").read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _parse_ts(raw: object) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        stamp = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp


def proven_recent(proven: dict, route: str, now: datetime | None = None) -> bool:
    """True when the route was probed within the TTL window."""
    last = _parse_ts((proven.get(route) or {}).get("last_probe"))
    if last is None:
        return False
    now = now or datetime.now(timezone.utc)
    return now - last < PROVEN_TTL


def record_proven(run_payload: dict, root: Path | None = None) -> Path:
    """Update data/proven.json from a run's candidate cells.

    last_probe = the run's started_at; statuses maps check_id -> status
    (skipped checks included, so a fail-fast death is visible too).
    The TTL stamp lands only on CONCLUSIVE outcomes: a transient "error"
    (one-off HTTP 5xx) records its status but must not start the
    no-reprobe freeze, so a recovering route is retried on the next sweep.
    """
    root = root or repo_root()
    proven = load_proven(root)
    stamp = run_payload.get("started_at") or datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    )
    for cell in run_payload.get("cells") or []:
        if not cell.get("candidate"):
            continue
        route = cell.get("alias") or ""
        if not route:
            continue
        status = cell.get("status")
        entry = proven.setdefault(route, {"statuses": {}})
        entry.setdefault("statuses", {})[cell.get("check_id") or ""] = status
        if status != "error":
            entry["last_probe"] = stamp
    path = root / "data" / "proven.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(proven, indent=2, sort_keys=True) + "\n")
    return path


def incumbent_bar(routes: dict, incumbents: list[str]) -> tuple[float | None, dict]:
    """(cheapest billed eff $/M, that entry) among the family's board aliases."""
    best: float | None = None
    best_entry: dict = {}
    for alias in incumbents:
        entry = routes.get(alias) or {}
        eff = entry.get("eff_per_mtok")
        if eff is None:
            continue
        if best is None or eff < best:
            best, best_entry = eff, entry
    return best, best_entry


def ask_bar(catalog: dict, incumbents: list[str]) -> float | None:
    """Worst-case billed $/M of the cheapest incumbent's catalog asks.

    Fallback bar for a board route with no billing history yet (a fresh
    swap): cache 0 and the fallback token mix, so the bar OVERSTATES the
    incumbent's likely bill rather than understating it — a challenger
    must beat the incumbent's raw asks to earn a probe.
    """
    best: float | None = None
    for alias in incumbents:
        ask = catalog.get(alias)
        if not ask:
            continue
        ask_in, ask_out = ask
        predicted = predicted_usd_m(
            ask_in, ask_out, 0.0, FALLBACK_W_IN, 1.0 - FALLBACK_W_IN
        )
        if best is None or predicted < best:
            best = predicted
    return best


def token_weights(entry: dict) -> tuple[float, float]:
    """(w_in, w_out) from the family token mix; 0.75/0.25 fallback."""
    try:
        tok_in = float(entry.get("tok_in") or 0)
        tok_out = float(entry.get("tok_out") or 0)
    except (TypeError, ValueError):
        return FALLBACK_W_IN, 1 - FALLBACK_W_IN
    total = tok_in + tok_out
    if total <= 0:
        return FALLBACK_W_IN, 1 - FALLBACK_W_IN
    return tok_in / total, tok_out / total


def predicted_usd_m(
    ask_in: float, ask_out: float, cache_rate: float, w_in: float, w_out: float
) -> float:
    """Predicted billed $/M — the family cache rate discounts the input ask."""
    return ask_in * (1.0 - cache_rate) * w_in + ask_out * w_out


def worst_case_predicted(row: dict, w_in: float, w_out: float) -> float:
    """Predicted $/M at ZERO cache — the most a route could possibly bill.

    Owner rule 2026-09-04: a parked route that would beat the incumbent
    bar even with no caching at all must not stay buried — its ask alone
    makes it cheaper than anything caching could rescue.
    """
    return predicted_usd_m(row["ask_in"], row["ask_out"], 0.0, w_in, w_out)


def family_context(pricing_routes: dict, aliases: list[str],
                   catalog: dict | None = None) -> dict[str, dict]:
    """Per family of the board: incumbent bar, cache rate, token weights.

    The bar is the cheapest BILLED effective $/M among the family's board
    aliases. A board route with no billing history yet (a fresh swap)
    falls back to its catalog asks when the catalog is supplied — see
    ask_bar(). bar_source names which one it is ("billed" / "ask").
    """
    families: dict[str, list[str]] = {}
    for alias in aliases:
        families.setdefault(family(alias), []).append(alias)
    out: dict[str, dict] = {}
    for fam, incumbents in families.items():
        bar, entry = incumbent_bar(pricing_routes, incumbents)
        bar_source = "billed" if bar is not None else None
        if bar is None and catalog is not None:
            bar = ask_bar(catalog, incumbents)
            if bar is not None:
                bar_source = "ask"
        out[fam] = {
            "bar": bar,
            "bar_source": bar_source,
            "cache_rate": (entry.get("cache_pct") or 0.0) / 100.0,
            "w_in": token_weights(entry)[0],
            "w_out": token_weights(entry)[1],
            "incumbents": incumbents,
        }
    return out


def rank_family(catalog: dict, fam: str, ctx: dict, exclude: set[str]) -> list[dict]:
    """Catalog routes of the family, cheapest predicted $/M first.

    Dated-only: plain tails of a dated family never rank as candidates
    (`dated_eligible` gate).
    """
    rows = []
    for route, (ask_in, ask_out) in catalog.items():
        if family(route) != fam or route in exclude:
            continue
        if not dated_eligible(route):
            continue
        rows.append(
            {
                "route": route,
                "ask_in": ask_in,
                "ask_out": ask_out,
                "predicted": predicted_usd_m(
                    ask_in, ask_out, ctx["cache_rate"], ctx["w_in"], ctx["w_out"]
                ),
            }
        )
    rows.sort(key=lambda r: (r["predicted"], r["route"]))
    return rows


def _pick(rows: list[dict], bar: float | None, proven: dict,
          w_in: float = 0.75, w_out: float | None = None,
          now: datetime | None = None) -> list[str]:
    """Top-N routes cheaper than the bar and outside the proven window.

    Rows arrive pre-filtered by `rank_family` (dated-only gate included).
    A route parked inside the TTL stays parked only when its probe was
    conclusively GOOD (every check "pass") — a parked FAIL does not bury
    a route whose worst-case (zero-cache) ask would still beat the bar
    (owner rule 2026-09-04): such a route re-enters the next sweep.
    """
    if bar is None:
        return []
    w_out = (1.0 - w_in) if w_out is None else w_out
    chosen: list[str] = []
    for row in rows:
        if row["predicted"] >= bar:
            break  # sorted ascending — nothing after this is cheaper
        if proven_recent(proven, row["route"], now):
            statuses = ((proven.get(row["route"]) or {}).get("statuses") or {})
            marks = list(statuses.values())
            if marks and all(s == "pass" for s in marks):
                continue  # conclusively good — nothing new to learn
            if worst_case_predicted(row, w_in, w_out) >= bar:
                continue  # failed AND not cheap even at zero cache
        chosen.append(row["route"])
        if len(chosen) >= TOP_N:
            break
    return chosen


def shortlist(key: str, pricing: dict | None, root: Path | None = None,
              now: datetime | None = None) -> list[dict]:
    """[{model, routes}] — the market's pick per board family.

    Gates: predicted $/M strictly under the incumbent bar; dated-only
    candidacy (plain tails of a dated family are never candidates); board
    aliases excluded; routes probed within the TTL skipped; at most TOP_N
    each.
    """
    root = root or repo_root()
    aliases = load_aliases()
    catalog = fetch_catalog(key)
    ctx = family_context((pricing or {}).get("routes") or {}, aliases, catalog)
    proven = load_proven(root)
    board = set(aliases)
    groups = []
    for fam in sorted(ctx):
        rows = rank_family(catalog, fam, ctx[fam], board)
        chosen = _pick(
            rows, ctx[fam]["bar"], proven,
            ctx[fam]["w_in"], ctx[fam]["w_out"], now,
        )
        if chosen:
            groups.append({"model": fam, "routes": chosen})
    return groups


def main(argv: list[str] | None = None, root: Path | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--dry-run" not in argv:
        print("usage: python3 -m probe.market --dry-run", file=sys.stderr)
        return 2
    key = os.environ.get("INFERHUB_API_KEY", "").strip()
    if not key:
        print("INFERHUB_API_KEY is required", file=sys.stderr)
        return 2
    root = root or repo_root()
    pricing = load_pricing(root)
    aliases = load_aliases()
    catalog = fetch_catalog(key)
    ctx = family_context((pricing or {}).get("routes") or {}, aliases, catalog)
    proven = load_proven(root)
    board = set(aliases)
    print(f"catalog routes with live asks: {len(catalog)}")
    total = 0
    for fam in sorted(ctx):
        info = ctx[fam]
        bar = info["bar"]
        if bar is None:
            bar_txt = "no incumbent bar"
        elif info.get("bar_source") == "ask":
            bar_txt = f"${bar:.4f}/M (ask-based — no billed usage yet)"
        else:
            bar_txt = f"${bar:.4f}/M"
        print(
            f"\n{fam}: incumbent bar {bar_txt} "
            f"(family cache {info['cache_rate']:.0%}, w_in {info['w_in']:.2f})"
        )
        rows = rank_family(catalog, fam, info, board)
        if not rows:
            print("  (no catalog routes)")
            continue
        chosen = _pick(rows, bar, proven, info["w_in"], info["w_out"])
        for row in rows:
            if row["route"] in chosen:
                # Mirror _pick's why: parked-only-if-conclusively-good law.
                mark = ("SHORTLIST (worst-case re-probe — failed probe, "
                        "cheap even at 0% cache)"
                        if proven_recent(proven, row["route"], None) else "SHORTLIST")
            elif bar is None:
                mark = "skip — no incumbent bar"
            elif row["predicted"] >= bar:
                mark = "skip — not cheaper"
            elif proven_recent(proven, row["route"], None):
                marks = list(((proven.get(row["route"]) or {}).get("statuses") or {}).values())
                mark = ("skip — proven <7d"
                        if marks and all(s == "pass" for s in marks)
                        else "skip — proven <7d (failed, not cheap even at 0% cache)")
            else:
                mark = "skip — proven <7d"
            print(
                f"  {row['route']:<40} predicted ${row['predicted']:.4f}/M "
                f"(ask {row['ask_in']:.4f}/{row['ask_out']:.4f}) {mark}"
            )
        print(f"  shortlist: {', '.join(chosen) if chosen else '(empty)'}")
        total += len(chosen)
    print(f"\nshortlist total: {total} route(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
