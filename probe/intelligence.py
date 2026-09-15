"""Fetch the Artificial Analysis intelligence snapshot for the board.

GET /api/v2/data/llms/models returns, per tracked model: the composite
Artificial Analysis Intelligence Index (9 evals: GPQA, HLE, SciCode,
Terminal-Bench, tau2, ...), a coding index, AA's own blended pricing and
speed medians. We snapshot only what the board needs to
data/intelligence.json each sweep. Like the catalog snapshot, this is
never hand-curated: the fetch is repeated every sweep so the numbers
cannot go stale (anti-staleness law, ONTOLOGY.md).

Free tier: 100 req/day, one call per sweep. Attribution: data comes from
artificialanalysis.ai; the board caption names the source.

Usage: python3 -m probe.intelligence   (needs AA_API_KEY in the env)
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from probe.registry import atomic_write_text, repo_root

AA_URL = "https://artificialanalysis.ai/api/v2/data/llms/models"
USER_AGENT = "inferhub-watch/1.0"
OUT_NAME = "intelligence.json"
CODING_FLOOR = 70.0  # standard fleet qualification floor for Coding IQ
GENERAL_IQ_TO_CODING_RATIO = 1.85  # empirical calibration factor (75/40) for unbenchmarked models


def resolve_coding_iq(intel_entry: dict | None, fallback_iq: float | None = None) -> float | None:
    """Resolve Coding IQ (0-100 scale) with calibrated fallback from General IQ.

    Returns the direct artificial_analysis_coding_index when present, or falls back to
    general_iq * 1.85 when only general composite IQ is available.
    """
    if intel_entry and isinstance(intel_entry, dict):
        coding = intel_entry.get("coding")
        if coding is not None:
            try:
                val = float(coding)
                if val > 0:
                    return val
            except (TypeError, ValueError):
                pass
        iq = intel_entry.get("iq")
        if iq is not None:
            try:
                val = float(iq)
                if val > 0:
                    return val * GENERAL_IQ_TO_CODING_RATIO
            except (TypeError, ValueError):
                pass

    if fallback_iq is not None:
        try:
            val = float(fallback_iq)
            if val > 0:
                return val * GENERAL_IQ_TO_CODING_RATIO
        except (TypeError, ValueError):
            pass

    return None



def _models_from_body(body: dict) -> dict[str, dict]:
    """Parse the AA response body into {slug: {iq, coding, aa_in, aa_out}}."""
    models: dict[str, dict] = {}
    for entry in body.get("data") or []:
        slug = entry.get("slug")
        if not slug:
            continue
        ev = entry.get("evaluations") or {}
        pricing = entry.get("pricing") or {}
        models[slug] = {
            "name": entry.get("name"),
            "iq": ev.get("artificial_analysis_intelligence_index"),
            "coding": ev.get("artificial_analysis_coding_index"),
            "aa_in": pricing.get("price_1m_input_tokens"),
            "aa_out": pricing.get("price_1m_output_tokens"),
        }
    return models


def fetch_models(key: str) -> dict[str, dict]:
    """Map AA slug -> {name, iq, coding, aa_in, aa_out} for every tracked model."""
    req = urllib.request.Request(
        AA_URL,
        headers={
            "x-api-key": key,
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = json.load(resp)
    return _models_from_body(body)


def write_snapshot(models: dict[str, dict], root: Path | None = None) -> Path:
    root = root or repo_root()
    out = root / "data" / OUT_NAME
    payload = {
        "generated_at": datetime.now(timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "artificialanalysis.ai",
        "endpoint": AA_URL,
        "models": models,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(out, json.dumps(payload, indent=1) + "\n")
    return out


def main() -> None:  # failure-tolerant: keep last good snapshot on any error
    key = os.environ.get("AA_API_KEY")
    if not key:
        print("AA_API_KEY not set; keeping previous intelligence snapshot")
        return
    try:
        models = fetch_models(key)
    except Exception as exc:  # noqa: BLE001 - sweep must stay green
        print(f"artificialanalysis fetch failed ({exc}); keeping previous snapshot")
        return
    if not models:
        print("artificialanalysis returned no models; keeping previous snapshot")
        return
    out = write_snapshot(models)
    print(f"wrote {out} ({len(models)} models)")


if __name__ == "__main__":
    sys.exit(main())
