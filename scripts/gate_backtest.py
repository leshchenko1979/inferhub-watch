#!/usr/bin/env python3
"""Both-scorings crown backtest — the gate's own question, asked twice.

Item 2 of the HQ dispatch (owner decision set 2026-09-12, D4): the projection
gate must be scored on BOTH the cost regret and the Value regret, and the
backtest must name `n`, `land` and `share` for each IN ONE RUN. This script is
that run.

WHAT IT DOES NOT DO. It does not crown anything and it does not decide which
leg the gate should pass on. Crown selection is the board's own ordering
(lowest projected $/M) for both legs — only the METRIC the regret ratio is
taken on differs (`probe/official_compare.py::_crown_regrets` vs
`_crown_value_regrets`). The ship-or-hold call is plan task 5; this artifact is
its evidence.

READING THE OUTPUT. `value_n` can be far below `n` and that is a RESOLUTION
fact, not a failure: the Value leg needs each snapshot's own D3 as-of `iq`
stamp, and a transition where either Value is unresolvable is dropped rather
than counted as a miss. A short `value_n` therefore says the input could not be
scored, never that Value scored badly — the same distinction the scoreboard's
`value_resolution` and the gate's `value_status` exist to preserve.

Usage:
    python3 scripts/gate_backtest.py            # human-readable
    python3 scripts/gate_backtest.py --json     # machine-readable
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from probe import official_compare, pricing  # noqa: E402


def _leg(label: str, n: int, land: int, share: float | None,
         tol: float, min_n: int, passed: bool) -> str:
    """One leg's line: n / land / share, with the bar it was measured against."""
    share_s = "n/a" if share is None else f"{share:.3f}"
    return (f"  {label:6s} n={n:3d}  land={land:3d}  share={share_s:>5s}  "
            f"pass={str(passed):5s}  (tol {tol}, min_n {min_n})")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Both-scorings crown backtest over the committed snapshots.")
    ap.add_argument("--json", action="store_true",
                    help="emit the gate dict as JSON instead of prose")
    args = ap.parse_args(argv)

    dated = pricing.dated_snapshots()
    gate = official_compare.projection_gate(dated)

    if args.json:
        print(json.dumps({
            "snapshots": len(dated),
            "first": dated[0][0] if dated else None,
            "last": dated[-1][0] if dated else None,
            "cost_regret": {
                "n": gate["n"], "land": gate["land"], "share": gate["share"],
                "tol": gate["tol"], "min_n": gate["min_n"],
                "pass": gate["pass"],
            },
            "value_regret": {
                "n": gate["value_n"], "land": gate["value_land"],
                "share": gate["value_share"], "tol": gate["value_tol"],
                "min_n": gate["value_min_n"], "pass": gate["value_pass"],
                "status": gate["value_status"],
            },
        }, indent=2))
        return 0

    span = f"{dated[0][0]} -> {dated[-1][0]}" if dated else "no snapshots"
    print(f"crown backtest over {len(dated)} committed snapshots ({span})")
    print(f"crown selection: the board's own ordering (lowest projected $/M), "
          f"both legs — only the regret metric differs")
    print()
    print(_leg("cost", gate["n"], gate["land"], gate["share"],
               gate["tol"], gate["min_n"], gate["pass"]))
    print(_leg("value", gate["value_n"], gate["value_land"],
               gate["value_share"], gate["value_tol"], gate["value_min_n"],
               gate["value_pass"]))
    print()
    print(f"  value status: {gate['value_status']}"
          + ("" if gate["value_status"] == "measured" else
             "  <- resolution limit of the input, NOT a verdict about Value"))
    print()
    print("  The gate's `pass` is the COST verdict. The Value leg is measured "
          "and reported\n  alongside it; which leg the gate ships on is the "
          "ship-or-hold decision, not this script's.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
