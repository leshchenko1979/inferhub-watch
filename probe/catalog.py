"""Fetch the inferhub catalog snapshot for the official-price comparison.

GET /api/catalog carries, per model: officialIn/officialOut (the upstream's
official $/Mtok rates - the baseline inferhub caps publisher asks at <=50%
of), the live asksIn/asksOut ladders, and a supportsCache flag. We snapshot
it to data/catalog.json each sweep so the site's official comparison never
reads a hand-maintained price table (curated prices go stale - learned
2026-08-31 when the ali hike made a 30d window misleading).

Cache billing rule (verified exactly from 6k+ billed rows, 2026-08-31):
cached input tokens bill at 10.0% of the input ask, on every route, in
every ask era. consumers of the snapshot can rely on CACHE_RATE.

Usage: python3 -m probe.catalog   (needs INFERHUB_API_KEY in the env)
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from probe.costs import MANAGEMENT, _get_json
from probe.registry import repo_root

CACHE_RATE = 0.1  # cached input ask = 10% of input ask (row-verified, all eras)

OUT_NAME = "catalog.json"


def fetch_models(key: str) -> dict[str, dict]:
    """Map 'prefix/upstreamModelId' -> official rates, cheapest asks, cache flag."""
    body = _get_json(f"{MANAGEMENT}/catalog", key)
    entries = body if isinstance(body, list) else body.get("rows") or []
    models: dict[str, dict] = {}
    for entry in entries:
        prefix = entry.get("prefix") or ""
        if not prefix or not entry.get("enabled"):
            continue
        for model in entry.get("models") or []:
            if not model.get("enabled") or model.get("modelDisabled"):
                continue
            name = model.get("upstreamModelId") or ""
            if not name:
                continue
            # Live asks ride pricePointsIn/Out as [price, provider_count] pairs
            # (asksIn/asksOut exist only on the /pricing page JSON, not the API).
            points_in = [float(p[0]) for p in model.get("pricePointsIn") or [] if len(p) > 1]
            points_out = [float(p[0]) for p in model.get("pricePointsOut") or [] if len(p) > 1]
            models[f"{prefix}/{name}"] = {
                "official_in": float(model.get("officialIn") or 0.0),
                "official_out": float(model.get("officialOut") or 0.0),
                "supports_cache": bool(model.get("supportsCache")),
                "ask_in": min(points_in) if points_in else None,
                "ask_out": min(points_out) if points_out else None,
            }
    return models


def snapshot_path(root: Path | None = None) -> Path:
    return (root or repo_root()) / "data" / OUT_NAME


def write_snapshot(models: dict[str, dict], root: Path | None = None) -> Path:
    path = snapshot_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cache_rate": CACHE_RATE,
        "models": models,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path


def main() -> int:  # noqa: BLE001 - catalog must never break the cron
    key = os.environ.get("INFERHUB_API_KEY") or ""
    if not key:
        print("catalog: INFERHUB_API_KEY not set, skipping")
        return 0
    try:
        models = fetch_models(key)
    except Exception as exc:  # keep yesterday's snapshot on any failure
        print(f"catalog: fetch failed ({exc}); keeping previous snapshot")
        return 0
    if not models:
        print("catalog: empty catalog response; keeping previous snapshot")
        return 0
    path = write_snapshot(models)
    print(f"catalog: wrote {len(models)} models to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
