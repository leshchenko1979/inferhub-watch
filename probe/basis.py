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

from probe import official_compare


def route_stats(payload: dict, route: str) -> dict:
    return (payload.get("routes") or {}).get(route) or {}


def realized(payload: dict, route: str) -> float | None:
    """Billed 30d effective $/M (None when the route has no billing)."""
    return route_stats(payload, route).get("eff_per_mtok")


def marginal(payload: dict, route: str) -> float | None:
    """Billed $/M since the prior snapshot (None without a fresh slice)."""
    return route_stats(payload, route).get("marginal_per_mtok")


def projected(payload: dict, route: str, dated: list) -> float | None:
    """Forward $/M at the smoothed projection hit rate; None without
    hit evidence or asks (callers fall back to realized)."""
    stats = route_stats(payload, route)
    hit, _conf = official_compare.projection_hit(
        dated, route, stats, payload.get("routes") or {}
    )
    return official_compare.inferhub_eff(stats, hit=hit)


def board_basis(payload: dict, route: str, dated: list, *,
                use_proj: bool) -> float | None:
    """The $/M the board ranks a route on: realized, or projected once
    the backtest gate passes. This is THE decision price."""
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


def incumbent_bar(routes: dict, incumbents: list[str]) -> float | None:
    """Cheapest billed eff $/M among the family's board aliases."""
    best: float | None = None
    for alias in incumbents:
        eff = (routes.get(alias) or {}).get("eff_per_mtok")
        if eff is not None and (best is None or eff < best):
            best = eff
    return best
