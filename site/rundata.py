from __future__ import annotations

import json
import math
from pathlib import Path


def load_runs(root: Path) -> list[dict]:
    files = sorted((root / "data" / "runs").glob("*.json"))
    return [json.loads(path.read_text()) for path in files]


def day_key(run: dict) -> str:
    return (run.get("started_at") or "")[:10]


def cell_map(run: dict) -> dict[tuple[str, str], dict]:
    return {(c["alias"], c["check_id"]): c for c in run.get("cells") or []}


def alias_probed(run: dict, alias: str) -> bool:
    """True when the run holds at least one cell for the alias."""
    return any(cell.get("alias") == alias for cell in run.get("cells") or [])


def scoring_ids(registry: list[dict]) -> list[str]:
    return [spec["id"] for spec in registry if spec.get("scores_rank")]


def display_specs(registry: list[dict]) -> list[dict]:
    scoring = [spec for spec in registry if spec.get("scores_rank")]
    rest = [spec for spec in registry if not spec.get("scores_rank")]
    return scoring + rest


def scoring_short(check_id: str) -> str:
    if check_id == "stream_tools":
        return "tools"
    if check_id == "cache_tools":
        return "cache"
    if check_id == "ru_mojibake":
        return "mojibake"
    return check_id.replace("_", " ")


def scoring_rule(check_ids: list[str]) -> str:
    n = len(check_ids)
    joined = " + ".join(scoring_short(check_id) for check_id in check_ids)
    return f"{n}/{n}: {joined}" if joined else f"{n}/{n}"


def scoring_pass_count(run: dict, alias: str, check_ids: list[str]) -> tuple[int, int]:
    cmap = cell_map(run)
    ok = sum(
        1
        for check_id in check_ids
        if (cell := cmap.get((alias, check_id))) and cell.get("status") == "pass"
    )
    return ok, len(check_ids)


def scoring_failed_ids(run: dict, alias: str, check_ids: list[str]) -> list[str]:
    cmap = cell_map(run)
    failed: list[str] = []
    for check_id in check_ids:
        cell = cmap.get((alias, check_id))
        if not cell or cell.get("status") != "pass":
            failed.append(check_id)
    return failed


def aliases_safe_first(
    aliases: list[str], run: dict, check_ids: list[str]
) -> list[str]:
    index = {alias: i for i, alias in enumerate(aliases)}

    def key(alias: str) -> tuple[int, int]:
        ok, total = scoring_pass_count(run, alias, check_ids)
        safe = 0 if total and ok == total else 1
        return (safe, index[alias])

    return sorted(aliases, key=key)


def run_stamp(run: dict) -> str:
    raw = run.get("started_at") or ""
    day = raw[5:10] if len(raw) >= 10 else day_key(run)
    clock = raw[11:16] if len(raw) >= 16 else ""
    return f"{day} {clock}".strip()


def origin_label(run: dict) -> str:
    raw = (run.get("origin") or "").strip()
    if raw == "github-actions":
        return "Actions · CI"
    if raw.endswith("-seed") or "seed" in raw:
        return "seed · fixture"
    return raw or "run"


def resolved_for_alias(run: dict, alias: str, registry: list[dict]) -> str:
    cmap = cell_map(run)
    resolved = ""
    for spec in registry:
        cell = cmap.get((alias, spec["id"])) or {}
        resolved = cell.get("resolved_model") or resolved
    return resolved


def cost_label(raw: object) -> str:
    """'0.000712' -> '$0.0007' (six decimals when too small). '' when absent."""
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    text = f"${value:.4f}"
    return text if text != "$0.0000" else f"${value:.6f}"


def _sum_costs(run: dict, alias: str | None = None) -> str:
    total = 0.0
    seen = False
    for cell in run.get("cells") or []:
        if alias is not None and cell.get("alias") != alias:
            continue
        raw = cell.get("cost_usdc")
        if not raw:
            continue
        try:
            total += float(raw)
            seen = True
        except ValueError:
            continue
    return cost_label(f"{total:.6f}") if seen else ""


def alias_run_cost(run: dict, alias: str) -> str:
    return _sum_costs(run, alias)


def run_total_cost(run: dict) -> str:
    return _sum_costs(run)


def load_pricing(root: Path) -> dict | None:
    """data/pricing.json as written by probe.pricing, or None when unusable."""
    try:
        payload = json.loads((root / "data" / "pricing.json").read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("routes"), dict):
        return None
    return payload


def pricing_rows(payload: dict | None) -> list[dict]:
    """Routes with any billed rate, in the order the probe wrote them."""
    if not payload:
        return []
    rows = []
    for route, entry in (payload.get("routes") or {}).items():
        if not isinstance(entry, dict):
            continue
        if entry.get("ask_in") is None and entry.get("eff_per_mtok") is None:
            continue
        rows.append({"route": route, **entry})
    return rows


def rate_label(raw: object, sig: int = 3) -> str:
    """Compact $/M label: '$0.014', '$2.49'; extra decimals when tiny."""
    try:
        value = float(str(raw))
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    text = f"${value:.{sig}g}"
    if "e" in text or "E" in text:
        text = f"${value:.6f}".rstrip("0").rstrip(".")
    return text


def token_label(count: int) -> str:
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.0f}k"
    return str(count)


def cache_label(raw: object) -> str:
    """68.7 -> '69%'; '' when absent."""
    try:
        return f"{float(str(raw)):.0f}%"
    except (TypeError, ValueError):
        return ""


# ── pricing-table visuals ──


def log_bar_pct(value: object, lo: float, hi: float) -> float:
    """Bar width % for value on a log10 scale between lo and hi, clamped.

    hi <= lo (a single non-zero peer) collapses to 100% for values at or
    above hi, so a one-row table still draws its bar.
    """
    try:
        v = float(str(value))
    except (TypeError, ValueError):
        return 0.0
    if v <= 0 or lo <= 0:
        return 0.0
    if hi <= lo:
        return 100.0 if v >= hi else 0.0
    pos = (math.log10(v) - math.log10(lo)) / (math.log10(hi) - math.log10(lo))
    return max(0.0, min(100.0, pos * 100.0))


def peer_bounds(values) -> tuple[float, float]:
    """(min, max) of the non-zero peers, or (0.0, 0.0) when there are none."""
    nonzero = [float(v) for v in values if v and float(v) > 0]
    if not nonzero:
        return (0.0, 0.0)
    return (min(nonzero), max(nonzero))


def rate_color_class(raw: object) -> str:
    """Bar color for an effective $/M rate: cheap green, pricey red."""
    try:
        v = float(str(raw))
    except (TypeError, ValueError):
        return ""
    if v <= 0.02:
        return "ok"
    if v <= 0.2:
        return "mid"
    return "bad"


def cache_color_class(raw: object) -> str:
    """Bar color for a cache-hit percentage: high green, low red."""
    try:
        v = float(str(raw))
    except (TypeError, ValueError):
        return ""
    if v >= 70:
        return "ok"
    if v >= 40:
        return "mid"
    return "bad"


def cache_bar_pct(raw: object) -> float:
    """Cache percentage clamped to a 0-100 bar width."""
    try:
        return max(0.0, min(100.0, float(str(raw))))
    except (TypeError, ValueError):
        return 0.0
