"""Official-price comparison math: "what you would pay at official rates".

Forward-looking per design v3-final (owner-approved 2026-08-31): every
number is computed from the LATEST billed asks + the trailing hit rate, so
nothing goes stale when publishers reprice (the ali qwen hike of 2026-08-29
made history-based comparisons misleading within a day).

Sources:
- data/pricing.json  - per-route latest billed ask, hit rate, 30d workload
- data/catalog.json  - official upstream rates + supportsCache (probe/catalog)

Cache rules:
- inferhub bills cached input at CACHE_RATE (10%) of the input ask -
  verified exactly across 6k+ billed rows in every ask era (2026-08-31).
- official endpoints: catalog only says supportsCache true/false, so we
  assume the same 10%-of-list hit price when supported, and NO discount
  when not. DeepSeek's real official hit price (2% of list) is lower, so
  the assumption UNDERSTATES official cost - conservative for "how much
  you would have paid".
"""

from __future__ import annotations

from probe.catalog import CACHE_RATE
from probe.market import family

MIN_REQS = 3  # below this the window is too thin to quote - cell shows a gap
DRIFT_TOL = 0.02  # predicted-vs-actual cost mismatch beyond 2% flags the cache rule


def _hit_rate(stats: dict) -> float | None:
    tok_in = stats.get("tok_in") or 0
    if tok_in <= 0:
        return None
    cached = stats.get("cached")
    if cached is None:
        # pricing.json route entries carry the share, not the raw count
        pct = stats.get("cache_pct")
        if pct is None:
            return None
        cached = tok_in * float(pct) / 100
    return cached / tok_in


def blended_eff(
    tok_in: float, tok_out: float, hit: float,
    in_miss: float, in_hit: float, out: float,
) -> float | None:
    """Blended $/Mtok over the actual in:out mix.

    tok_* in tokens, prices in $/Mtok. None when there is no traffic.
    """
    total = tok_in + tok_out
    if total <= 0:
        return None
    m_in, m_out = tok_in / 1e6, tok_out / 1e6
    cost = m_in * ((1 - hit) * in_miss + hit * in_hit) + m_out * out
    return cost / (total / 1e6)


def inferhub_eff(stats: dict) -> float | None:
    """What the route's workload costs NOW at its latest billed asks."""
    ask_in, ask_out = stats.get("ask_in"), stats.get("ask_out")
    hit = _hit_rate(stats)
    if ask_in is None or ask_out is None or hit is None:
        return None
    return blended_eff(
        stats.get("tok_in") or 0, stats.get("tok_out") or 0, hit,
        ask_in, CACHE_RATE * ask_in, ask_out,
    )


def official_eff(stats: dict, model: dict) -> float | None:
    """Same workload at official upstream rates, same hit rate.

    Hit price = CACHE_RATE x official_in when the upstream supports cache,
    else full official_in (no discount assumed - conservative).
    """
    official_in, official_out = model.get("official_in"), model.get("official_out")
    hit = _hit_rate(stats)
    if not official_in or not official_out or hit is None:
        return None
    in_hit = CACHE_RATE * official_in if model.get("supports_cache") else official_in
    return blended_eff(
        stats.get("tok_in") or 0, stats.get("tok_out") or 0, hit,
        official_in, in_hit, official_out,
    )


def drift_flag(stats: dict) -> bool:
    """True when the snapshot's measured cache rule deviates from CACHE_RATE.

    The ratio is solved per billed row at snapshot time (see cache_rule_stats)
    and stored in pricing.json as hit_ask_ratio; aggregate cost math cannot
    distinguish an ask-era mix from a real rule change, so the check must be
    row-exact. None (not measured) never flags.
    """
    ratio = stats.get("hit_ask_ratio")
    return ratio is not None and abs(ratio - CACHE_RATE) > DRIFT_TOL


def cache_rule_stats(rows: list[dict], model: str) -> float | None:
    """Median implied cached-input ask ÷ input ask over one model's rows.

    Each row with cached tokens is a one-unknown equation:
        cost = (prompt - cached)/1e6*ask_in + cached/1e6*hit_ask
               + completion/1e6*ask_out
    The median needs >=5 solvable rows (below that the window is too thin
    to accuse the platform of changing its rule). None = unchecked.
    """
    ratios: list[float] = []
    for row in rows:
        if (row.get("model") or "") != model:
            continue
        cached = _num(row.get("cached_tokens"))
        ask_in = _num(row.get("ask_input_per_mtok"))
        ask_out = _num(row.get("ask_output_per_mtok")) or 0.0
        cost = _num(row.get("cost_consumer_usdc"))
        prompt = _num(row.get("prompt_tokens"))
        completion = _num(row.get("completion_tokens")) or 0.0
        if not (cached and cached > 0 and ask_in and ask_in > 0 and cost and prompt):
            continue
        resid = cost - (prompt - cached) / 1e6 * ask_in - completion / 1e6 * ask_out
        if resid <= 0:
            continue
        ratios.append(resid / (cached / 1e6) / ask_in)
    if len(ratios) < 5:
        return None
    ratios.sort()
    mid = len(ratios) // 2
    if len(ratios) % 2:
        return ratios[mid]
    return (ratios[mid - 1] + ratios[mid]) / 2


def _num(raw: object) -> float | None:
    try:
        value = float(str(raw))
    except (TypeError, ValueError):
        return None
    return value


def comparison_rows(pricing: dict, catalog: dict) -> list[dict]:
    """One decision row per pricing route that maps into the catalog.

    Row fields: route, reqs, hit_pct, ih_eff, off_eff, ratio, drift,
    note (why a number is a gap). Unknown-family routes are kept with a
    note so the page shows what it could not compare - it never crashes
    the build.
    """
    models = catalog.get("models") or {}
    catalog_families: dict[str, str] = {}
    for key in models:
        catalog_families.setdefault(family(key), key)
    rows: list[dict] = []
    for alias in sorted(pricing.get("routes") or {}):
        stats = pricing["routes"][alias] or {}
        if not (stats.get("reqs") or 0):
            continue  # no traffic in the window - no workload to compare
        model = models.get(alias) or models.get(catalog_families.get(family(alias), ""))
        row = {
            "route": alias,
            "reqs": stats.get("reqs") or 0,
            "hit_pct": round(100 * _hit_rate(stats), 1) if _hit_rate(stats) is not None else None,
            "ih_eff": None,
            "off_eff": None,
            "ratio": None,
            "drift": drift_flag(stats),
            "note": None,
        }
        if model is None:
            row["note"] = "not in catalog"
            rows.append(row)
            continue
        if (stats.get("reqs") or 0) < MIN_REQS:
            row["note"] = f"thin window (<{MIN_REQS} reqs)"
            rows.append(row)
            continue
        ih = inferhub_eff(stats)
        off = official_eff(stats, model)
        if ih is None:
            row["note"] = "missing billed ask in window"
        elif off is None:
            row["note"] = "missing official rate in catalog"
        else:
            row["ih_eff"] = round(ih, 4)
            row["off_eff"] = round(off, 4)
            row["ratio"] = round(off / ih, 1) if ih > 0 else None
        rows.append(row)
    return rows
