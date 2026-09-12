"""Value — the North Star metric. Single owner of the formula.

    Value = (IQ / eff) * (tps / tps_ref) ** TPS_WEIGHT_EXPONENT

The objective the system optimizes: the TPS-adjusted realized ask a route
delivers (ONTOLOGY.md, **North Star metric**). The predictor forecasts it,
the scoreboard scores it, the switcher's crown maximizes it.

WHY THIS MODULE EXISTS. Before it, the formula was written out in three
places — `scripts/auto_route_switch.py::calculate_value` (the switcher's
runtime path, which carries an extra raw-ask-mix fallback and stays where it
is), `scripts/predictor_scoreboard.py` (the score), and the board's Value
column — with the constants duplicated as bare literals in each. A metric
defined three times drifts three ways, and the reader cannot tell which one
is authoritative. The READ surfaces (board, scoreboard) now delegate here;
the switcher keeps its own implementation of the SAME formula, because it is
owner-gated runtime and its behaviour must not change under a refactor.

The exponent and the reference are owner property (`TPS_WEIGHT_EXPONENT`
0.50, `DEFAULT_TPS_REF` 50.0), NOT tuning knobs: changing either changes
what the North Star measures.

`eff` is supplied by the CALLER, because the two surfaces rank on different
money bases and that is deliberate: the board passes its own ranking basis
(`probe.basis::board_basis`, the cost-transparency basis) while the switcher
passes its blended ask. Value is a formula over an eff, not a basis owner —
`probe/basis.py` owns money bases.
"""

from __future__ import annotations

TPS_WEIGHT_EXPONENT = 0.50   # owner-confirmed square-root power law
DEFAULT_TPS_REF = 50.0       # fallback reference when no fleet prior exists


def fleet_tps_ref(payload: dict | None) -> float:
    """The dynamic fleet median TPS prior, or the default reference.

    Delegates to `probe.official_compare.fleet_tps_prior`, which applies the
    n >= 5 evidence bar and the 1..500 plausibility window over the payload's
    perf block. A route with too little production evidence is normalized
    against its PEERS rather than against a hardcoded constant, so a fleet
    that gets faster does not silently inflate every Value.
    """
    from probe import official_compare  # local: official_compare imports basis
    prior = official_compare.fleet_tps_prior(payload)
    return float(prior) if prior else DEFAULT_TPS_REF


def tps_of(payload: dict | None, route: str, ref: float) -> float:
    """The route's production tps_mean, or the reference when it is thin.

    The n >= 5 floor is the evidence bar (ONTOLOGY.md **TPS Weight**) and is
    applied HERE, at the consumer, as well as in `perf_stats` — a route below
    it falls back to the reference, which makes the TPS weight 1.0 and leaves
    Value governed by IQ per $ alone. That fallback is deliberate but silent
    in the number, so callers that can show it should say which routes took
    it (see the scoreboard's `tps_samples`).
    """
    stats = ((payload or {}).get("perf") or {}).get("models") or {}
    entry = stats.get(route) or {}
    tps = entry.get("tps_mean")
    n = entry.get("tps_samples") or 0
    if tps is not None and n >= 5 and 1.0 <= float(tps) <= 500.0:
        return float(tps)
    return ref


def value_of(iq: float | None, eff: float | None, tps: float,
             ref: float) -> float | None:
    """Value, or None when the route cannot be scored at all.

    None is NOT zero: a route with no IQ or no positive eff has no Value,
    and a caller must render a gap rather than a bottom-of-the-table 0.0.
    """
    if not eff or eff <= 0 or not iq or iq <= 0:
        return None
    return (iq / eff) * ((tps or ref) / ref) ** TPS_WEIGHT_EXPONENT
