"""Build the frozen render-test corpus (P2a): data/fixture/*.json.

Freezes a slim copy of the committed data (board routes only, generated_at
pinned) so site render tests run against a stable contract instead of the
moving nightly data. Run: python3 tests/make_fixtures.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from probe.registry import load_aliases, repo_root

FIXTURES = repo_root() / "tests" / "fixtures"
PINNED_GENERATED_AT = "2026-09-07T09:12:37+00:00"


def main() -> None:
    root = repo_root()
    FIXTURES.mkdir(exist_ok=True)
    aliases = load_aliases()

    pricing = json.loads((root / "data" / "pricing.json").read_text())
    pricing["generated_at"] = PINNED_GENERATED_AT
    slim_routes = {}
    for route, entry in (pricing.get("routes") or {}).items():
        if route in aliases or entry.get("candidate"):
            slim_routes[route] = entry
    pricing["routes"] = slim_routes
    # days: keep the last 5, they drive the spend sparkline contract
    pricing["days"] = (pricing.get("days") or [])[-5:]
    # W6 slimming parity: committed data may still predate the slimming;
    # fixtures always carry the post-W6 shape (flag in, raw ts list out).
    from probe.pricing import _probe_only_buildtime, _probe_windows_from_runs
    windows = _probe_windows_from_runs(root)
    for route, entry in slim_routes.items():
        ts = entry.pop("marginal_ts", None)
        if ts is None:
            continue
        reqs = int(entry.get("marginal_reqs") or 0)
        truncated = bool(entry.get("marginal_ts_truncated"))
        entry["probe_only"] = _probe_only_buildtime(
            ts, reqs, truncated, windows, route)
    (FIXTURES / "pricing.json").write_text(json.dumps(pricing, indent=2) + "\n")

    runs_dir = root / "data" / "runs"
    latest = max(runs_dir.glob("*.json"))
    run = json.loads(latest.read_text())
    aliases_set = set(aliases)
    run["cells"] = [c for c in run.get("cells") or []
                    if c.get("alias") in aliases_set]
    (FIXTURES / "run-latest.json").write_text(json.dumps(run, indent=2) + "\n")

    intel = json.loads((root / "data" / "intelligence.json").read_text())
    (FIXTURES / "intelligence.json").write_text(json.dumps(intel, indent=2) + "\n")
    print(f"fixtures written to {FIXTURES}: "
          f"{len(slim_routes)} routes, 1 run ({latest.name})")


if __name__ == "__main__":
    main()
