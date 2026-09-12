"""D3 backfill: stamp `iq` + `tps_mean`/`tps_samples` into dated snapshots.

Owner decision 2026-09-12 (D3). A historical North Star / Value backtest needs
the two inputs the predictor actually had on the day, and neither is
recoverable later:

- **IQ** lives only in `data/intelligence.json`, which every sweep OVERWRITES.
  Today's file is not what a past day saw, so the value is read from that
  file's own git history at the commit in force on the snapshot's date.
- **TPS** is read from the snapshot's OWN `perf` block where it carries one.
  `perf` was added partway through the series, so early snapshots have no
  per-route speed at all.

Where either is genuinely unavailable the field is stamped `null`. Never
interpolated, never guessed, never filled from a neighbouring day: a fabricated
IQ would silently launder into a Value crown, and the whole point of D3 is to
make the Value backtest possible WITHOUT inventing its inputs.

Idempotent: re-running rewrites the same three keys and leaves everything else
byte-identical (the file is re-emitted in the builder's own `indent=2` form).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from probe.registry import aa_slug  # noqa: E402

PRICING_DIR = ROOT_DIR / "data" / "pricing"
INTEL_PATH = "data/intelligence.json"


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(ROOT_DIR), *args],
        capture_output=True, text=True, check=True,
    ).stdout


def _intel_commit_for(day: str) -> str | None:
    """Newest commit touching intelligence.json on or before `day` (end of day)."""
    out = _git("log", "--before", f"{day} 23:59:59 +0000", "-1",
               "--format=%H", "--", INTEL_PATH).strip()
    return out or None


def _iq_map(commit: str | None) -> dict:
    """slug -> iq from intelligence.json at `commit`.

    `None` means the file did not exist at that date (it was introduced
    2026-09-01), so there was no IQ to stamp — an EMPTY map, never a fallback
    to the working tree. Falling back would stamp today's IQ onto a day that
    could not have seen it, which is exactly the look-ahead contamination D3
    exists to prevent.
    """
    if commit is None:
        return {}
    try:
        raw = _git("show", f"{commit}:{INTEL_PATH}")
    except subprocess.CalledProcessError:
        return {}
    try:
        models = (json.loads(raw).get("models") or {})
    except ValueError:
        return {}
    return {slug: entry.get("iq") for slug, entry in models.items()
            if isinstance(entry, dict)}


def backfill(day: str, payload: dict, iq_map: dict) -> dict:
    """Add the three D3 keys to every route entry; return per-key counts."""
    perf_models = ((payload.get("perf") or {}).get("models") or {})
    counts = {"iq": 0, "tps_mean": 0}
    for route, entry in (payload.get("routes") or {}).items():
        if not isinstance(entry, dict):
            continue
        slug = aa_slug(route)
        iq = iq_map.get(slug) if slug else None
        entry["iq"] = iq
        if iq is not None:
            counts["iq"] += 1
        ps = perf_models.get(route) or {}
        entry["tps_mean"] = ps.get("tps_mean")
        entry["tps_samples"] = ps.get("tps_samples")
        if ps.get("tps_mean") is not None:
            counts["tps_mean"] += 1
    return counts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    files = sorted(PRICING_DIR.glob("*.json"))
    print(f"{len(files)} dated snapshots")
    print(f"{'day':12s} {'routes':>6s} {'iq':>5s} {'tps':>5s}  intel commit")
    for path in files:
        day = path.stem
        payload = json.loads(path.read_text())
        commit = _intel_commit_for(day)
        iq_map = _iq_map(commit)
        counts = backfill(day, payload, iq_map)
        print(f"{day:12s} {len(payload.get('routes') or {}):6d} "
              f"{counts['iq']:5d} {counts['tps_mean']:5d}  "
              f"{(commit or 'none')[:10]}")
        if not args.dry_run:
            path.write_text(json.dumps(payload, indent=2) + "\n")
    if args.dry_run:
        print("\n(dry run — nothing written)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
