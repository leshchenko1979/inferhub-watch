"""Verdict ticket: the dispatch recommendation at the top of the board.

Split out of generate.py (P2b section-module carve). Ranks routes on the
same money basis as the board (probe.basis), stamps the runner-up as the
alternate, renders the ask-trend spark.
"""
from __future__ import annotations

import html

import rundata
from probe import basis, official_compare
from probe.registry import repo_root

from spend import _ask_spark

ROOT = repo_root()


def _proj_eff(dated: list, payload: dict, route: str) -> float | None:
    """The forward-looking $/M for one route — delegates to probe.basis
    (P1a single money-basis owner). None without hit evidence or asks."""
    return basis.projected(payload, route, dated)

def verdict_section(payload: dict | None) -> str:
    """The dispatch ticket at the top of the board, or ''.

    Answers the site's one question — where to route bulk workload today —
    from the same sweep data as the board: the best IQ-per-$ route, its
    reason (ask trend, cache hit), the runner-up as the stamped alternate.
    IQ per $ ranks on the projection once the backtest gate passes
    (projection_gate), on the realized 30d eff before that. Never
    hardcodes a model; '' when intelligence or effective prices are
    missing so the plain board stands alone.
    """
    if not payload:
        return ""
    intel = rundata.load_intelligence(ROOT)
    models = (intel or {}).get("models") or {}
    dated = rundata.load_dated_pricing(ROOT)
    use_proj = bool(official_compare.projection_gate(dated).get("pass"))
    ranked: list[tuple[float, dict]] = []
    for row in rundata.pricing_rows(payload):
        slug = rundata.aa_slug(str(row["route"]))
        entry = models.get(slug) if slug else None
        iq = entry.get("iq") if entry else None
        eff = row.get("eff_per_mtok")
        if use_proj:
            proj = _proj_eff(dated, payload, str(row["route"]))
            if proj is not None:
                eff = proj
        try:
            ratio = iq / eff if iq is not None and eff else None
        except ZeroDivisionError:
            ratio = None
        if ratio:
            ranked.append((ratio, row))
    # Billed evidence beats catalog floor: prefer usage-logged routes when
    # any qualify, so the ticket never recommends an unverified floor ask.
    logged = [(ratio, row) for ratio, row in ranked if row.get("source") == "usage-logs"]
    ranked = logged or ranked
    if not ranked:
        return ""
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    best_ratio, best = ranked[0]
    route = str(best["route"])
    floor = best.get("source") != "usage-logs"

    # Ask trend: consecutive strictly-cheaper snapshots from the newest end.
    series = rundata.ask_series(dated, route, payload)
    downs = 0
    for (_, a0, o0), (_, a1, o1) in zip(series, series[1:]):
        if a1 + o1 < a0 + o0:
            downs += 1
        else:
            break
    why: list[str] = []
    if downs >= 2:
        why.append(f"ask down {downs} sweeps straight")
    elif downs == 1:
        why.append("ask easing")
    elif len(series) >= 2:
        why.append("ask holding")
    if floor:
        why.append("floor ask — no billed traffic yet")
    hit = best.get("cache_pct")
    if hit is not None:
        why.append(f"{hit:.0f}% cached")
    if use_proj:
        proj = _proj_eff(dated, payload, route)
        if proj is not None:
            label = rundata.rate_label(proj, fixed=4) or "n/a"
            why.append(f"projects {label} $/M now")

    why_html = ""
    if why:
        why_html = '<p class="ticket-why">' + " &#183; ".join(html.escape(w) for w in why) + "</p>"
    alt = ""
    if len(ranked) > 1:
        alt_ratio, alt_row = ranked[1]
        alt = (
            f'<p class="ticket-alt">Alternate: <code>{html.escape(str(alt_row["route"]))}</code>'
            f" &#8212; {alt_ratio:,.0f} IQ per $</p>"
        )
    spark = _ask_spark(series)
    return (
        '<section class="ticket" id="verdict">'
        '<div class="ticket-main">'
        '<div class="ticket-facts">'
        '<p class="ticket-line">Route bulk here</p>'
        f'<h2 class="ticket-route"><code>{html.escape(route)}</code></h2>'
        f'<p class="ticket-big">{best_ratio:,.0f}'
        '<span class="ticket-unit"> IQ per $</span></p>'
        + why_html
        + "</div>"
        + (f'<div class="ticket-spark">{spark}</div>' if spark else "")
        + "</div>"
        + alt
        + "</section>"
    )


