"""Money basis (P1a): the single owner of "what does a route cost".

Every price surface on the site — board rate cell, verdict ticket,
scatter X, radar incumbent bar — must rank/consume prices from HERE, so
the same route can never show three different numbers on the same day.

Three bases per route:
- realized: billed cost over the full window (30d) — the bill.
- marginal: billed cost since the prior snapshot — "what has it cost
  since yesterday" (fragile on probe-swept quiet routes; tagged).
- projected: current billed asks priced at the smoothed projection hit
  rate — the forward view, trusted only once projection_gate passes.
"""
from __future__ import annotations

from pathlib import Path

from probe import official_compare, pricing


def route_stats(payload: dict, route: str) -> dict:
    return (payload.get("routes") or {}).get(route) or {}


def realized(payload: dict, route: str) -> float | None:
    """Billed 30d effective $/M (None when the route has no billing)."""
    return route_stats(payload, route).get("eff_per_mtok")


def marginal(payload: dict, route: str) -> float | None:
    """Billed $/M since the prior snapshot (None without a fresh slice)."""
    return route_stats(payload, route).get("marginal_per_mtok")


def fleet_calibration_multiplier(dated: list, min_pairs: int = 3) -> float:
    """Causal expanding fleet median calibration multiplier (Strategy 2).

    Computes the historical median ratio (realized / raw_projected) strictly
    across past snapshot transitions available in `dated` up to the as-of moment.
    Returns 1.0 if fewer than `min_pairs` historical transitions are available.
    Preserves 100% of relative cost/value ordering while centering the level ratio.
    """
    if not isinstance(dated, list) or len(dated) < 2:
        return 1.0
    ratios: list[float] = []
    for k in range(len(dated) - 1):
        older = dated[k][1] if isinstance(dated[k], (tuple, list)) else None
        newer = dated[k + 1][1] if isinstance(dated[k + 1], (tuple, list)) else None
        if not isinstance(older, dict) or not isinstance(newer, dict):
            continue
        older_routes = older.get("routes") or {}
        newer_routes = newer.get("routes") or {}
        hist_k = dated[: k + 1]
        for r_name, st in older_routes.items():
            if not isinstance(st, dict) or (st.get("reqs") or 0) < 3:
                continue
            real_eff = (newer_routes.get(r_name) or {}).get("eff_per_mtok")
            if not real_eff or real_eff <= 0:
                continue
            hit_k, _ = official_compare.projection_hit(hist_k, r_name, st, older_routes)
            p_raw = official_compare.inferhub_eff(st, hit=hit_k)
            if not p_raw or p_raw <= 0:
                continue
            ratios.append(float(real_eff) / float(p_raw))
    if len(ratios) < min_pairs:
        return 1.0
    import statistics
    return float(statistics.median(ratios))


def projected(payload: dict, route: str, dated: list, *,
              calibrated: bool = True) -> float | None:
    """Forward $/M at the smoothed projection hit rate; None without
    hit evidence or asks (callers fall back to realized).

    When calibrated=True (default), scales by the causal online fleet median
    multiplier (Strategy 2) to correct historical floor-to-realized level bias
    without distorting relative route rankings.
    """
    stats = route_stats(payload, route)
    hit, _conf = official_compare.projection_hit(
        dated, route, stats, payload.get("routes") or {}
    )
    p_raw = official_compare.inferhub_eff(stats, hit=hit)
    if p_raw is None or not calibrated:
        return p_raw
    kappa = fleet_calibration_multiplier(dated)
    return p_raw * kappa


def board_basis(payload: dict, route: str, dated: list, *,
                use_proj: bool) -> float | None:
    """The $/M the board ranks a route on: realized, or projected once
    the crown gate passes. This is THE decision price."""
    if use_proj:
        proj = projected(payload, route, dated)
        if proj is not None:
            return proj
    return realized(payload, route)


def chart_price(payload: dict, route: str) -> tuple[float | None, str]:
    """(price, basis_tag) for the scatter X axis — owner order: the chart
    plots MARGINAL price, falling back to realized where absent."""
    m = marginal(payload, route)
    if m is not None:
        return m, "marginal"
    r = realized(payload, route)
    if r is not None:
        return r, "eff"
    return None, ""


def gate_state(root: Path | None = None) -> dict:
    """The projection gate verdict over the committed dated snapshots.

    The gate is a property of the money basis, so it is read through this
    owner (probe.pricing.dated_snapshots -> official_compare.projection_gate)
    and published to Postgres for the Grafana panels — one implementation,
    one verdict, both surfaces. Recomputed from committed history, never
    hardcoded.
    """
    return official_compare.projection_gate(pricing.dated_snapshots(root))

def route_bases(payload: dict, dated: list) -> list[dict]:
    """Every route's money bases, exactly as the board computes them.

    The Grafana panels cannot read the committed snapshot, so their price
    must come from HERE or the two surfaces rank different routes on the
    same day (issue #15: the panels divided an ungated projected basis and
    led with a route the board did not). One owner, one price.
    """
    out: list[dict] = []
    for route in (payload.get("routes") or {}):
        out.append({
            "route": route,
            "realized": realized(payload, route),
            "projected": projected(payload, route, dated),
        })
    return out

def incumbent_bar(routes: dict, incumbents: list[str]) -> float | None:
    """Cheapest billed eff $/M among the family's board aliases."""
    best: float | None = None
    for alias in incumbents:
        eff = (routes.get(alias) or {}).get("eff_per_mtok")
        if eff is not None and (best is None or eff < best):
            best = eff
    return best


def fleet_tps_prior(payload_or_perf: dict | None) -> float | None:
    """Median throughput (TPS) across routes with confident production traffic."""
    return official_compare.fleet_tps_prior(payload_or_perf)

