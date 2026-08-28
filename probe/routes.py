"""Probe arbitrary provider/model routes — the provider-discovery driver.

`probe.run` probes the board (aliases from models.toml). This tool probes
anything: pass route strings on the command line and it runs every scoring
check against each one, printing a comparison table. It is a research tool
for finding safe providers — no run JSON is written and the board is
untouched.

Usage:
    INFERHUB_API_KEY=... python3 -m probe.routes cb/gpt-5.6-luna cmc/deepseek/deepseek-v4-pro
"""

from __future__ import annotations

import os
import sys

from probe.http import InferHubClient
from probe.registry import load_check_module, load_registry
from probe.run import balance_too_low


def scoring_specs(registry: list[dict]) -> list[dict]:
    """Only the checks that rank routes — info checks are skipped on sweeps."""
    return [spec for spec in registry if spec.get("scores_rank")]


def probe_routes(
    client: InferHubClient, routes: list[str], registry: list[dict]
) -> list[dict]:
    """Run every scoring check against every route; cells come back in order.

    A check that raises is recorded as an `error` cell so one dead route does
    not stop the sweep. Fail-fast: a fail or error cell skips the remaining
    checks of that route. Stops early when InferHub reports balance too low.
    """
    cells = []
    for route in routes:
        specs = scoring_specs(registry)
        for i, spec in enumerate(specs):
            module = load_check_module(spec["id"])
            try:
                cell = module.run(client, route)
            except Exception as exc:  # noqa: BLE001 — record, keep sweeping
                if balance_too_low(str(exc)):
                    print(
                        f"balance too low — sweep stopped at {route}/{spec['id']}",
                        file=sys.stderr,
                    )
                    return cells
                cell = {
                    "check_id": spec["id"],
                    "status": "error",
                    "summary": str(exc),
                }
            cells.append({"route": route, **cell})
            if cell.get("status") in ("fail", "error"):
                for remaining in specs[i + 1 :]:
                    cells.append(
                        {
                            "route": route,
                            "check_id": remaining["id"],
                            "status": "skipped",
                            "summary": "not run — earlier check failed",
                        }
                    )
                break
    return cells


def format_table(cells: list[dict]) -> str:
    lines = []
    for cell in cells:
        evidence = cell.get("evidence") or {}
        extra = ""
        if isinstance(evidence, dict) and "hit_ratio" in evidence:
            extra = f" cache={evidence['hit_ratio']:.0%}"
        summary = (cell.get("summary") or "").replace("\n", " ")[:110]
        lat = cell.get("latency_ms")
        lat_s = f"{lat:>6}ms" if isinstance(lat, int) else "      -"
        lines.append(
            f"{cell.get('route', '?'):36} {cell.get('check_id', '?'):14} "
            f"{cell.get('status', '?'):6} {lat_s}{extra} {summary}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("usage: python3 -m probe.routes ROUTE [ROUTE ...]", file=sys.stderr)
        return 2
    key = os.environ.get("INFERHUB_API_KEY", "").strip()
    if not key:
        print("INFERHUB_API_KEY is required", file=sys.stderr)
        return 2
    client = InferHubClient(key)
    cells = probe_routes(client, argv, load_registry())
    print(format_table(cells))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
