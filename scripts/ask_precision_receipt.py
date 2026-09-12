#!/usr/bin/env python3
"""Ask-precision receipt (#27 Finding A): raw vs stored realized ask.

`probe.pricing.route_entry` is the ONE place a route's stored `eff_per_mtok`
is produced. The quantizer there, `round(eff, 4)`, is an ABSOLUTE decimal
snap: it fixes the step at 1e-4 whatever the magnitude, so across the board's
ask range (1e-4 .. 1e-1) it leaves one or two significant figures and
manufactures ties between genuinely different routes.

This script replays the committed snapshot's OWN raw inputs (`cost_usdc`,
`tok_in`, `tok_out`) through the real `route_entry`, and reports per route:

    raw     the true effective $/M = cost / tokens * 1e6
    stored  what route_entry writes into the artifact
    err     (stored - raw) / raw

then hunts COLLAPSES: distinct raw asks that store to the same value. A
collapse at the head of the board is the defect, because `site/board.py`
sorts on `(eff_val, -iqps)` — on an equal ask the higher IQ per $ wins, so
the board leads with the DEARER route.

Zero production mutation: reads the committed snapshot, writes nothing,
touches no config, no session, no notification. Exit 0 when no collapse is
found among routes with billed traffic, 1 when one is.

Usage:  python3 scripts/ask_precision_receipt.py [--json]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from probe import pricing  # noqa: E402

REPO = Path(__file__).resolve().parents[1]

# The four routes named in the #27 Finding A table, plus the incumbent pair
# whose collapse is the receipted defect.
NAMED = [
    "cb/deepseek-v4.1-flash",
    "ag/gemini-3.7-flash-high",
    "ag/gemini-3.8-flash-high",
    "ali/deepseek-v4-flash-0731",
]


def _stats_from_entry(entry: dict) -> dict | None:
    """The raw inputs `route_entry` consumes, recovered from the artifact.

    `cost_usdc` / `tok_in` / `tok_out` are stored unquantized (the cost string
    carries the full 6-dp billed sum, the token counts are exact integers), so
    these are the same inputs the builder saw — only `eff_per_mtok` was
    snapped on the way out. That makes this a faithful replay.
    """
    if entry.get("source") != "usage-logs":
        return None
    try:
        cost = float(entry.get("cost_usdc"))
    except (TypeError, ValueError):
        return None
    tok_in = int(entry.get("tok_in") or 0)
    tok_out = int(entry.get("tok_out") or 0)
    if tok_in + tok_out <= 0:
        return None
    return {
        "reqs": entry.get("reqs") or 0,
        "tok_in": tok_in,
        "tok_out": tok_out,
        "cached": 0,
        "cost": cost,
        "ask_in": entry.get("ask_in"),
        "ask_out": entry.get("ask_out"),
        "last_ts": entry.get("last_ts") or "",
        "hit_ask_ratio": entry.get("hit_ask_ratio"),
    }


def raw_eff(stats: dict) -> float:
    """The unquantized effective $/M — the number the snap is applied to."""
    toks = stats["tok_in"] + stats["tok_out"]
    return stats["cost"] / toks * 1e6


def replay(snapshot: dict) -> list[dict]:
    """Re-run route_entry over every billed route; report raw vs stored."""
    routes = snapshot.get("routes") or {}
    out = []
    for route, entry in sorted(routes.items()):
        stats = _stats_from_entry(entry)
        if stats is None:
            continue
        rebuilt = pricing.route_entry(stats, {}, route)
        raw = raw_eff(stats)
        stored = rebuilt.get("eff_per_mtok")
        out.append({
            "route": route,
            "reqs": stats["reqs"],
            "tok_in": stats["tok_in"],
            "tok_out": stats["tok_out"],
            "cost_usdc": stats["cost"],
            "raw": raw,
            "stored": stored,
            "artifact": entry.get("eff_per_mtok"),
            "err_pct": ((stored - raw) / raw * 100.0) if stored is not None and raw else None,
        })
    return out


def lattice_count(rows: list[dict]) -> int:
    """Rows whose stored ask sits exactly on the 1e-4 lattice."""
    hits = 0
    for row in rows:
        stored = row["stored"]
        if stored is None:
            continue
        if abs(stored * 1e4 - round(stored * 1e4)) < 1e-9:
            hits += 1
    return hits


def collapses(rows: list[dict], rel_tol: float = 1e-6) -> list[tuple[dict, dict]]:
    """Pairs of distinct raw asks that store to the same value."""
    found = []
    for i, a in enumerate(rows):
        for b in rows[i + 1:]:
            if a["stored"] is None or b["stored"] is None:
                continue
            if a["stored"] != b["stored"]:
                continue
            lo, hi = sorted((a["raw"], b["raw"]))
            if hi and (hi - lo) / hi > rel_tol:
                found.append((a, b))
    return found


def board_order(payload: dict, use_proj: bool) -> list[tuple[str, float]]:
    """The board's rank key over a payload: (eff_val, -IQ/$) ascending.

    Mirrors `site/board.py:_iq_sort_key` — the realized/projected $/M
    ascending, then IQ per $ descending. Reimplemented here rather than
    imported so the receipt does not drag the whole render stack in; the
    ordering law it encodes is asserted against the real board in
    tests/test_switcher_basis_shifts.py's sibling precision test.
    """
    from probe import basis
    from probe.registry import aa_slug

    intel = json.loads((REPO / "data" / "intelligence.json").read_text())
    models = intel.get("models") or {}
    dated = pricing.dated_snapshots(REPO)
    ordered = []
    for route, entry in (payload.get("routes") or {}).items():
        eff = basis.board_basis(payload, route, dated, use_proj=use_proj)
        if not eff:
            continue
        slug = aa_slug(route)
        iq = (models.get(slug) or {}).get("iq") if slug else None
        iqps = (iq / eff) if (iq is not None and eff) else None
        ordered.append((route, eff, iqps))
    ordered.sort(key=lambda r: (r[1], -(r[2] if r[2] is not None else float("-inf"))))
    return [(r[0], r[1]) for r in ordered]


def switcher_leg(payload: dict, named: list[str]) -> dict:
    """Drive the REAL switcher dry-run over the committed payload.

    What the switcher reads for its realized basis is `resolve_realized_price`
    -> `probe.basis.realized` -> the stored `eff_per_mtok`, so this leg shows
    both the per-route value the switcher sees AND the decision it reaches.

    Every live surface is patched to RAISE, so an accidental production write
    fails loudly instead of mutating the fleet — same guard shape as
    `scripts/basis_shift_harness.py`, whose counters are measured, not asserted.
    """
    from contextlib import ExitStack
    from datetime import datetime, timezone
    from unittest import mock

    from probe import pgstore
    from scripts import auto_route_switch as ars

    reads = {}
    for route in named:
        price, samples = ars.resolve_realized_price(route, payload)
        reads[route] = {"realized_price": price, "perf_samples": samples}

    guards = [
        mock.patch.object(pgstore, "window_rows", return_value=[]),
        mock.patch.object(ars, "resolve_inferhub_key", return_value=""),
        mock.patch.object(ars, "update_opencrabs_config",
                          mock.MagicMock(side_effect=AssertionError("config write"))),
        mock.patch.object(ars, "update_active_sessions",
                          mock.MagicMock(side_effect=AssertionError("session write"))),
        mock.patch.object(ars, "send_session_notification",
                          mock.MagicMock(side_effect=AssertionError("notify"))),
        mock.patch.object(ars, "resolve_notify_session",
                          mock.MagicMock(side_effect=AssertionError("notify session"))),
    ]
    config_before = (ars.DEFAULT_CONFIG_PATH.read_bytes()
                     if ars.DEFAULT_CONFIG_PATH.exists() else None)
    with ExitStack() as stack:
        for guard in guards:
            stack.enter_context(guard)
        res = ars.run_auto_route_switch(
            root_dir=REPO,
            config_path=ars.DEFAULT_CONFIG_PATH,
            dry_run=True,
            notify=False,
            now=datetime.now(timezone.utc),
        )
    config_after = (ars.DEFAULT_CONFIG_PATH.read_bytes()
                    if ars.DEFAULT_CONFIG_PATH.exists() else None)
    return {
        "reads": reads,
        "basis_mode": res.get("basis_mode"),
        "should_switch": res.get("should_switch"),
        "current_model": res.get("current_model"),
        "best_model": res.get("best_model"),
        "reason": res.get("reason"),
        "dwell": res.get("dwell"),
        "live_config_unchanged": config_before == config_after,
    }


def main() -> int:
    as_json = "--json" in sys.argv
    snapshot = json.loads((REPO / "data" / "pricing.json").read_text())
    rows = replay(snapshot)
    by_route = {r["route"]: r for r in rows}

    rebuilt_payload = json.loads(json.dumps(snapshot))
    for route, row in by_route.items():
        rebuilt_payload["routes"][route]["eff_per_mtok"] = row["stored"]

    named = [by_route[r] for r in NAMED if r in by_route]
    lat = lattice_count(rows)
    col = collapses(rows)

    if as_json:
        print(json.dumps({
            "generated_at": snapshot.get("generated_at"),
            "routes_with_billing": len(rows),
            "on_lattice": lat,
            "collapses": [
                {"a": a["route"], "a_raw": a["raw"], "b": b["route"], "b_raw": b["raw"],
                 "stored": a["stored"],
                 "gap_pct": (max(a["raw"], b["raw"]) - min(a["raw"], b["raw"]))
                            / max(a["raw"], b["raw"]) * 100.0}
                for a, b in col
            ],
            "named": named,
        }, indent=2))
        return 1 if col else 0

    print(f"snapshot {snapshot.get('generated_at')}  range={snapshot.get('range')}  "
          f"rows={snapshot.get('requests_scanned')}  source={snapshot.get('row_source')}")
    print(f"routes with billed traffic: {len(rows)}\n")

    print(f"{'route':<30} {'reqs':>7} {'raw $/M':>14} {'stored':>10} {'err':>9}")
    print("-" * 74)
    for row in named:
        err = f"{row['err_pct']:+.2f}%" if row["err_pct"] is not None else "-"
        print(f"{row['route']:<30} {row['reqs']:>7} {row['raw']:>14.9f} "
              f"{str(row['stored']):>10} {err:>9}")

    print(f"\nstored asks sitting exactly on the 1e-4 lattice: {lat}/{len(rows)}")

    if col:
        print(f"\nCOLLAPSES (distinct raw asks, one stored value): {len(col)}")
        for a, b in col:
            gap = (max(a["raw"], b["raw"]) - min(a["raw"], b["raw"])) / max(a["raw"], b["raw"]) * 100.0
            print(f"  stored {a['stored']}:  {a['route']} raw {a['raw']:.9f}  vs  "
                  f"{b['route']} raw {b['raw']:.9f}   gap {gap:.2f}%")
    else:
        print("\nCOLLAPSES: none — every pair of distinct raw asks stores distinctly.")

    print("\nboard rank under the REALIZED basis (the fallback path):")
    for i, (route, eff) in enumerate(board_order(rebuilt_payload, False)[:6], 1):
        print(f"  {i}. {route:<30} {eff:.9f}")

    if "--no-switcher" not in sys.argv:
        try:
            leg = switcher_leg(rebuilt_payload, [r for r in NAMED if r in by_route])
        except Exception as exc:  # noqa: BLE001 — receipt leg, never fatal
            print(f"\nswitcher leg skipped: {exc}")
        else:
            print("\nwhat the SWITCHER reads for its realized basis:")
            for route, read in leg["reads"].items():
                print(f"  {route:<30} {read['realized_price']!r}  "
                      f"(perf samples {read['perf_samples']})")
            print(f"\nswitcher dry-run: basis={leg['basis_mode']} "
                  f"should_switch={leg['should_switch']} dwell={leg['dwell']}")
            print(f"  {leg['reason']}")
            print(f"  live config unchanged: {leg['live_config_unchanged']}")

    return 1 if col else 0


if __name__ == "__main__":
    raise SystemExit(main())
