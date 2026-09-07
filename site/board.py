"""Cost board: pricing table, scatter chart, and the evidence layer.

Split out of generate.py (P2b section-module carve). One section per
reader question — the money board (effective/now pairs, IQ per $), the
marginal-price scatter, and the collapsed evidence tables (official
comparison, reliability, main-traffic speed). All money figures come
from probe.basis (P1a single money-basis owner).
"""
from __future__ import annotations

import html
import math

import rundata
from probe import basis, official_compare, pricing

from probe.registry import repo_root

from chrome import _viz_cell, section_title
from spend import DELTA_TIP, _ask_spark, ask_delta_bits, spend_block

ROOT = repo_root()

def _probe_only(row: dict, runs: list[dict]) -> bool:
    """True when every marginal request for this route falls inside a sweep
    window that probed it — the route's only fresh traffic is probes, so
    the marginal $/M is real money but an unrepresentative (cache-cold,
    tiny-prompt) workload.

    W6 slimming: snapshots built after the slimming carry the precomputed
    `probe_only` flag and no ts list; use it when present. Older snapshots
    (and test fixtures) still carry the raw ts list, so the original
    analysis stays as fallback: a truncated list (or a single ts outside
    every window) means working traffic exists.

    Timestamps are compared as datetimes, never as strings: usage-log
    stamps end in Z while run-window stamps end in +00:00, and lexical
    order between the two formats is wrong (Z sorts above '+')."""
    if "probe_only" in row:
        return bool(row["probe_only"])
    reqs = int(row.get("marginal_reqs") or 0)
    ts_list = row.get("marginal_ts") or []
    if not reqs or row.get("marginal_ts_truncated") or len(ts_list) < reqs:
        return False
    windows = []
    for run in runs:
        start = pricing.parse_ts(run.get("started_at"))
        end = pricing.parse_ts(run.get("finished_at"))
        if start and end:
            windows.append((start, end, set(run.get("aliases") or [])))
    if not windows:
        return False
    route = str(row.get("route") or "")
    for ts in ts_list:
        parsed = pricing.parse_ts(ts)
        if parsed is None or not any(
            route in al and s <= parsed <= e for s, e, al in windows
        ):
            return False
    return True

def _plumb_row(cells: list[tuple[str, str]], colspan: int = 3) -> str:
    """One collapsed plumbing row under a board route: <details><dl>.

    Keys arrive pre-rendered (callers escape any data-derived text)."""
    pairs = "".join(f"<div><dt>{k}</dt><dd>{v}</dd></div>" for k, v in cells)
    return (
        f'<tr class="plumb-row"><td colspan="{colspan}"><details>'
        "<summary>Show plumbing</summary>"
        f'<dl class="plumb">{pairs}</dl></details></td></tr>'
    )

def _route_failures(payload: dict | None, route: str, reqs: int) -> str:
    """Per-route failure line for the plumbing row, '' without stats."""
    if not payload:
        return ""
    stats = (payload.get("failures") or {}).get("by_model") or {}
    entry = stats.get(route)
    if not entry:
        return "0 in window"
    failed, seen = int(entry.get("failed") or 0), int(entry.get("reqs") or 0)
    codes = entry.get("codes") or {}
    line = f"{failed} / {seen} req"
    if codes:
        line += " (" + ", ".join(f"{c} &times; {n}" for c, n in sorted(codes.items())) + ")"
    return line

def _route_retries(runs: list[dict], route: str) -> str:
    """Per-route retry evidence from the latest run — '' when no sweep had
    to replay this route. Recovered cells passed on their second attempt;
    'still down' cells did not recover on either attempt."""
    if not runs:
        return ""
    latest = runs[-1]
    recovered = down = 0
    for cell in rundata.board_cells(latest) + rundata.candidate_cells(latest):
        if cell.get("alias") != route:
            continue
        if cell.get("flaky_recovered"):
            recovered += 1
        elif cell.get("first_attempt"):
            down += 1
    parts = []
    if recovered:
        parts.append(f"{recovered} recovered on retry")
    if down:
        parts.append(f"{down} still down after retry")
    return "; ".join(parts)

def _board_basis(row: dict, dated: list | None, payload: dict,
                 use_proj: bool) -> float | None:
    """The $/M the board ranks a route on — delegates to probe.basis
    (P1a single money-basis owner)."""
    return basis.board_basis(payload, str(row["route"]), dated or [],
                             use_proj=use_proj)

def _iq_sort_key(intel: dict, dated: list | None, payload: dict,
                 use_proj: bool):
    """Board order: realized $/M ascending (IA law: the board answers
    "what is this costing me" — cheapest realized cost first). Ties and
    routes without an eff figure sink last; IQ per $ remains the
    secondary discriminator within an equal cost band."""
    def key(row: dict) -> tuple:
        eff = _board_basis(row, dated, payload, use_proj)
        try:
            eff_val = eff if eff else float("inf")
        except ZeroDivisionError:
            eff_val = float("inf")
        slug = rundata.aa_slug(str(row["route"]))
        entry = (intel.get("models") or {}).get(slug) if slug else None
        iq = entry.get("iq") if entry else None
        iqps = None
        if iq is not None and eff:
            try:
                iqps = iq / eff
            except ZeroDivisionError:
                iqps = None
        return (eff_val, -(iqps if iqps is not None else float("-inf")))
    return key
    def key(row: dict) -> float:
        slug = rundata.aa_slug(str(row["route"]))
        entry = (intel.get("models") or {}).get(slug) if slug else None
        iq = entry.get("iq") if entry else None
        eff = _board_basis(row, dated, payload, use_proj)
        try:
            return iq / eff if iq is not None and eff else float("-inf")
        except ZeroDivisionError:
            return float("-inf")
    return key

def _pair_cell(eff_label: str, proj_label: str | None,
               use_proj: bool) -> tuple[str, str, str]:
    """The rate cell pairing the two eras; bold = the basis IQ per $ ranks on."""
    if use_proj and proj_label is not None:
        main_val, main_tag, alt_val, alt_tag = proj_label, "now", eff_label, "30d"
        data_label = "projected $/M"
        data_tip = (
            "Forward cost per M tokens at current billed asks (median ask, "
            "smoothed hit rate); 30d is the realized window average."
        )
    else:
        main_val, main_tag = eff_label, "30d"
        alt_val, alt_tag = proj_label, "now"
        data_label = "effective $/M"
        data_tip = (
            "Billed cost per M tokens over all traffic in the window, cache "
            'discounts included. The "now" figure is the forward '
            "projection at current billed asks (median ask, smoothed hit rate)."
        )
    pair = (
        f'<span class="pair-main">{main_val}'
        f'<span class="pair-tag">{main_tag}</span></span>'
    )
    if alt_val:
        pair += (
            f'<span class="pair-alt">{alt_val}'
            f'<span class="pair-tag">{alt_tag}</span></span>'
        )
    return pair, data_label, data_tip

def _marginal_cell(row: dict, runs: list[dict]) -> tuple[str, str] | None:
    """The marginal $/M plumbing cell; dimmed when traffic is probe-only."""
    if row.get("marginal_per_mtok") is None:
        return None
    marg_label = rundata.rate_label(row.get("marginal_per_mtok"), fixed=4) or "n/a"
    since_day = str(row.get("marginal_since") or "")[:10]
    marg_tip = (
        "Billed cost per M over requests since the previous daily "
        f"snapshot ({html.escape(since_day)}) &#8212; the fair "
        "comparator when a price has just moved."
    )
    if _probe_only(row, runs):
        marg_label = f'<span class="dim">{marg_label}</span>'
        marg_tip += (
            " Dimmed: probe-only traffic &#8212; every request in the "
            "window is a sweep probe, so the workload (cache-cold, "
            "tiny prompts) is unrepresentative."
        )
    return (f"marginal $/M <span title=\"{marg_tip}\">&#9432;</span>", marg_label)

def _pricing_caption(span: str, use_proj: bool, gate: dict) -> str:
    """The board caption: reading guide + live gate state."""
    return (
        "&#8220;ask&#8221; under each route is the per-M rate billed on fresh "
        "(uncached) input / output; &#8220;effective&#8221; is billed cost over all "
        "tokens, cache discounts included. Each rate cell pairs the two eras and "
        "tags each: &#8220;now&#8221; is the forward projection at current billed "
        "asks (median ask, smoothed hit rate) and &#8220;30d&#8221; the realized "
        "window figure &#8212; the bold one is the basis IQ "
        f"per $ ranks on ({'projection' if use_proj else 'realized'} basis, "
        f"backtest gate {'passed' if use_proj else 'not passed'}: "
        f"{gate.get('within')}/{gate.get('n')} transitions within "
        f"{int((gate.get('tol') or 0.2) * 100)}%). "
        "Effective bars are log-scaled over "
        "$0.001&#8211;$10 per M and colored teal &#8804; $0.02, amber above. "
        "Show plumbing folds each route&#8217;s ask movement (&#916; ask in / out vs "
        "the previous daily snapshot: &#8595; teal cheaper, &#8593; amber pricier, "
        "&#8212; no earlier snapshot to compare yet), ask source, ask history, "
        "cache hit, failures, window traffic and cost, IQ, and retries (when a "
        "sweep replayed a route). "
        "Marginal $/M is billed cost over requests since the previous daily "
        "snapshot, dimmed when the route&#8217;s only fresh traffic is sweep "
        "probes (real money, unrepresentative workload). "
        "* = floor ask &#8212; catalog minimum, shown when the route has no billed traffic in the window. "
        "IQ = Artificial Analysis Intelligence Index (composite of 9 public evals, "
        "artificialanalysis.ai, effort level max), refreshed every sweep; IQ per $ divides it by the route&#8217;s effective $/M &#8212; higher is smarter per dollar. "
        "Sparkline bars are log-scaled $0.001&#8211;$10 per day. "
        "Rates for this board&#8217;s routes only; other traffic is not listed."
    )

def pricing_section(payload: dict | None, runs: list[dict]) -> str:
    """The #pricing section, or '' when there is no usable pricing data."""
    rows = rundata.pricing_rows(payload)
    if not rows:
        return ""
    span = html.escape(str(payload.get("range") or "30d"))
    scanned = payload.get("requests_scanned")
    note = f"{span} window"
    if scanned:
        note += f" · {scanned} billed requests"
    dated = rundata.load_dated_pricing(ROOT)
    prior = rundata.prior_pricing(dated, payload)
    intel = rundata.load_intelligence(ROOT)
    gate = official_compare.projection_gate(dated)
    use_proj = bool(gate.get("pass"))
    rows.sort(key=_iq_sort_key(intel, dated, payload, use_proj), reverse=False)
    body_rows = []
    for row in rows:
        logged = row.get("source") == "usage-logs"
        mark = "" if logged else '<span class="ask-mark" title="floor ask &#8212; catalog minimum, no billed traffic yet">*</span>'
        cand = ' <span class="cand-mark" title="radar candidate &#8212; also billed here, see candidates section">&#9666; cand</span>' if row.get("candidate") else ""
        ask_in = rundata.rate_label(row.get("ask_in"), fixed=4) or "n/a"
        ask_out = rundata.rate_label(row.get("ask_out"), fixed=4) or "n/a"
        reqs = int(row.get("reqs") or 0)
        toks = rundata.token_label(
            int(row.get("tok_in") or 0) + int(row.get("tok_out") or 0)
        )
        # Decision row: two figures a scanner reads — the rate pair and
        # IQ per $. Everything else (ask movement, history, cache,
        # traffic, cost, failures, source) folds into the plumbing row.
        proj = _proj_eff(dated, payload, str(row["route"]))
        ranking_basis = _board_basis(row, dated, payload, use_proj)
        eff_label = rundata.rate_label(row.get("eff_per_mtok"), fixed=4) or "n/a"
        proj_label = rundata.rate_label(proj, fixed=4) if proj is not None else None
        pair, data_label, data_tip = _pair_cell(eff_label, proj_label, use_proj)
        iq = _iq_value(str(row["route"]), ranking_basis, intel)
        series = rundata.ask_series(dated, str(row["route"]), payload)
        body_rows.append(
            "<tr>"
            f'<th scope="row"><code>{html.escape(str(row["route"]))}</code>{cand}'
            f'<span class="route-ask">ask {ask_in} / {ask_out} per M{mark}</span></th>'
            + _viz_cell(
                pair,
                rundata.log_bar_pct(ranking_basis, 0.001, 10.0),
                rundata.rate_color_class(ranking_basis),
                data_label=data_label,
                data_tip=data_tip,
            )
            + '<td class="num" data-label="IQ per $" '
            'data-tip="Intelligence (Artificial Analysis index) divided by the '
            'route&#8217;s ranking $/M &#8212; higher is smarter per dollar.">'
            f"{iq[1] if iq else '&#8212;'}</td>"
            + "</tr>"
        )
        plumb_cells = [
            ("&#916; ask in / out",
             f'<span title="{DELTA_TIP}">'
             f'{ask_delta_bits(payload, prior, str(row["route"]))}</span>'),
            ("ask source", "billed ask" if logged else "floor ask"),
            ("cache hit", rundata.cache_label(row.get("cache_pct")) or "n/a"),
            ("failures", _route_failures(payload, str(row["route"]), reqs)),
            (f"{span} traffic", f"{reqs} req · {toks} tok"),
            (f"{span} cost", rundata.cost_label(row.get("cost_usdc")) or "n/a"),
        ]
        spark = _ask_spark(series)
        if spark:
            plumb_cells.append(("ask history", spark))
        if iq:
            plumb_cells.append(("IQ", iq[0]))
        if retries := _route_retries(runs, str(row["route"])):
            plumb_cells.append(("retries", retries))
        if (marg := _marginal_cell(row, runs)) is not None:
            plumb_cells.append(marg)
        body_rows.append(_plumb_row(plumb_cells))
    caption = _pricing_caption(span, use_proj, gate)
    from tmpl import render

    return render(
        "pricing.html",
        title=html.escape(section_title("pricing")),
        note=html.escape(note),
        caption=caption,
        body_rows="".join(body_rows),
        after=(
            scatter_section(payload, intel, runs)
            + spend_block(payload, runs)
            + evidence_block(payload, rundata.load_catalog(ROOT), dated)
        ),
    )

def usage_color(reqs: int | None, probe_only: bool = False) -> str:
    """Point color (owner orders 2026-09-07): teal = in use (real billed
    traffic in the window — probe-only routes do NOT count as in use),
    amber = probes only (the only fresh traffic is sweep probes: the
    marginal price is real money but an unrepresentative cache-cold
    workload), gray = no traffic at all."""
    if probe_only:
        return "var(--warn, #c90)"
    if not reqs or reqs <= 0:
        return "var(--muted)"
    return "var(--ok)"

def _scatter_legend() -> str:
    return (
        '<div class="scatter-legend">'
        '<span><svg width="12" height="12"><circle cx="6" cy="6" r="5" fill="var(--ok)"/></svg>'
        " in use (real traffic/24h)</span>"
        '<span><svg width="12" height="12"><circle cx="6" cy="6" r="5" fill="var(--warn, #c90)"/></svg>'
        " probes only</span>"
        '<span><svg width="12" height="12"><circle cx="6" cy="6" r="5" fill="var(--muted)"/></svg>'
        " not in use</span>"
        "</div>"
    )

def scatter_section(payload: dict | None, intel: dict | None, runs: list[dict] | None = None) -> str:
    """Price vs IQ scatter (inline SVG, zero-dependency) or ''.

    X = MARGINAL $/M (the recent billed rate — owner order: "price =
    marginal price"), falling back to realized eff when a route has no
    marginal sample yet; cheaper = right, matching the board's money law.
    Y = AA IQ (higher = up); point color = 24h billed requests (recent
    usage). Every point carries data-tip, so the shared tooltip engine
    covers touch and hover. Routes without BOTH a price and an IQ are
    excluded (counted in the caption); the section is skipped without a
    snapshot.
    """
    if not payload or not intel:
        return ""
    perf = ((payload.get("perf") or {}).get("models")) or {}
    hours = (payload.get("perf") or {}).get("window_hours") or 24
    points = []
    skipped = 0
    for row in rundata.pricing_rows(payload):
        route = str(row["route"])
        # X price + basis tag from probe.basis (P1a single money-basis owner).
        price, tag = basis.chart_price(payload, route)
        eff = price
        basis_tag = tag
        slug = rundata.aa_slug(route)
        entry = (intel.get("models") or {}).get(slug) if slug else None
        iq = entry.get("iq") if isinstance(entry, dict) else None
        if not isinstance(eff, (int, float)) or eff <= 0 or not isinstance(iq, (int, float)):
            skipped += 1
            continue
        runs = runs or []
        ponly = _probe_only(row, runs)
        points.append((route, float(eff), iq,
                       int((perf.get(route) or {}).get("reqs") or 0), basis_tag,
                       ponly))
    if len(points) < 2:
        return ""
    # Frame: log-x over the realized price range, y over IQ.
    xs = [p[1] for p in points]
    ys = [p[2] for p in points]
    x_lo, x_hi = min(xs) / 1.5, max(xs) * 1.5
    y_lo, y_hi = max(0.0, min(ys) - 5), max(ys) + 5
    W, H, PAD_L, PAD_R, PAD_T, PAD_B = 640, 300, 44, 12, 14, 34

    def sx(eff: float) -> float:
        lo, hi = math.log10(x_lo), math.log10(x_hi)
        return PAD_L + (math.log10(eff) - lo) / (hi - lo) * (W - PAD_L - PAD_R)

    def sy(iq: float) -> float:
        return PAD_T + (1 - (iq - y_lo) / (y_hi - y_lo)) * (H - PAD_T - PAD_B)

    log_lo, log_hi = math.log10(x_lo), math.log10(x_hi)
    grid = []
    step = (log_hi - log_lo) / 4
    for i in range(5):
        v = 10 ** (log_lo + i * step)
        gx = PAD_L + i * (W - PAD_L - PAD_R) / 4
        grid.append(
            f'<line x1="{gx:.1f}" y1="{PAD_T}" x2="{gx:.1f}" y2="{H - PAD_B}" class="sg"/>'
            f'<text x="{gx:.1f}" y="{H - PAD_B + 14}" class="st" text-anchor="middle">'
            f"{rundata.rate_label(v, fixed=4)}</text>"
        )
    # IQ gridlines at nice steps of 10.
    iq_lo, iq_hi = int(math.floor(y_lo / 10) * 10), int(math.ceil(y_hi / 10) * 10)
    for v in range(iq_lo, iq_hi + 1, 10):
        gy = sy(v)
        if PAD_T <= gy <= H - PAD_B:
            grid.append(
                f'<line x1="{PAD_L}" y1="{gy:.1f}" x2="{W - PAD_R}" y2="{gy:.1f}" class="sg"/>'
                f'<text x="{PAD_L - 6}" y="{gy + 3:.1f}" class="st" text-anchor="end">{v}</text>'
            )
    dots = ""
    # Label-collision pass (QA finding: cx/cb pairs at identical y printed
    # on top of each other). Track occupied y rows; nudge a colliding label
    # down/up by rows of ~13px until free, both sides, so every name reads.
    occupied: list[float] = []
    LABEL_STEP = 13.0
    for route, eff, iq, reqs, basis_tag, ponly in points:
        cx, cy = sx(eff), sy(iq)
        # Label right of the dot; flip left when it would clip the frame.
        label_right = cx < W - PAD_R - 130
        lx = cx + 10 if label_right else cx - 10
        anchor = "start" if label_right else "end"
        ly = cy + 3
        guard = 0
        while any(abs(ly - oy) < LABEL_STEP * 0.9 for oy in occupied) and guard < 6:
            guard += 1
            ly = cy + 3 + guard * LABEL_STEP * (1 if guard % 2 else -1)
            if ly < PAD_T + 8 or ly > H - PAD_B - 4:
                ly = cy + 3  # reset and try the other direction next round
        occupied.append(ly)
        tip = (
            f"{html.escape(route)} &#8212; {rundata.rate_label(eff, fixed=4)} $/M ({basis_tag}) &#183; "
            f"IQ {iq:.1f} &#183; {reqs} reqs/{hours}h"
            + (" &#183; probes only" if ponly else "")
        )
        dots += (
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="6" fill="{usage_color(reqs, ponly)}" '
            f'data-tip="{tip}" '
            f'aria-label="{html.escape(route)}"/>'
            f'<text x="{lx:.1f}" y="{ly:.1f}" class="slabel" text-anchor="{anchor}" '
            f'data-tip="{tip}">{html.escape(route)}</text>'
        )
    caption = (
        "Marginal $/M vs AA IQ, in use = billed traffic in the newest "
        f"{hours}h. Right = cheaper, up = smarter. Log x-axis; eff fallback "
        f"for routes without a marginal sample; routes without a price or an "
        f"IQ mapping are not plotted ({skipped} skipped)."
    )
    return (
        '<figure class="scatter">'
        f"<figcaption>{caption}</figcaption>"
        f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{html.escape(caption)}">'
        f"{''.join(grid)}{dots}"
        '<text x="50%" y="12" class="st" text-anchor="middle">AA IQ</text>'
        "</svg>"
        f"{_scatter_legend()}"
        "</figure>"
    )

def perf_table(payload: dict | None) -> str:
    """Main-traffic speed sub-table for the evidence layer, or ''.

    Built from the `perf` block probe.pricing aggregates into
    data/pricing.json — usage-log timings (ttft_ms/duration_ms) over ALL
    traffic in the newest 24h of the 30d window, production requests
    included. Absent on snapshots written before this block existed.
    """
    if not payload:
        return ""
    perf = payload.get("perf") or {}
    models = perf.get("models") or {}
    if not models:
        return ""
    hours = perf.get("window_hours") or 24
    rows = []
    for model, m in models.items():
        ttft = m.get("ttft_p50_ms")
        tps = m.get("tps_mean")
        ttft_label = f"{ttft / 1000:.2f}s" if isinstance(ttft, (int, float)) else "&#8212;"
        tps_label = f"{tps:.1f}" if isinstance(tps, (int, float)) else "&#8212;"
        rows.append((int(m.get("reqs") or 0), model, m, ttft_label, tps_label))
    rows.sort(key=lambda r: (-r[0], r[1]))  # traffic weight first, then name
    body = "".join(
        "<tr>"
        f'<th scope="row"><code>{html.escape(str(model))}</code></th>'
        f'<td class="num" data-tip="All InferHub requests billed for this model in the newest {hours}h window — production traffic, not just probes.">{reqs}</td>'
        f'<td class="num" data-tip="Median time to first token across this model&#8217;s billed requests in the window (p50).">{ttft_label}</td>'
        f'<td class="num" data-tip="Mean tokens/sec of generation time only (duration minus ttft); rows with a &lt;2s generation window or impossible tps are excluded.">{tps_label}</td>'
        "</tr>"
        for reqs, model, _m, ttft_label, tps_label in rows
    )
    caption = (
        f"Every request billed in the newest {hours}h — production traffic included, "
        "not just probes. ttft = median time to first token; tps = mean tokens/sec "
        "of generation time only (duration minus ttft); dash = no timed requests."
    )
    return (
        '<div class="scroll"><table class="pricing">'
        f"<caption>{caption}</caption>"
        "<thead><tr>"
        '<th scope="col">Route</th>'
        '<th scope="col" class="num">requests</th>'
        '<th scope="col" class="num">ttft p50</th>'
        '<th scope="col" class="num">tps mean</th>'
        "</tr></thead>"
        f"<tbody>{body}</tbody>"
        "</table></div>"
    )

def evidence_block(payload: dict | None, catalog: dict | None,
                   dated: list | None = None) -> str:
    """The EVIDENCE layer: official-price, reliability, main-traffic speed.

    Sits at the bottom of #pricing; collapsed by default so the decision
    surface (verdict + board) stays uncluttered. Renders only when at
    least one sub-table has data. `dated` snapshots switch the official
    table's forward columns onto the smoothed projection hit rate.
    """
    official = official_table(payload, catalog, dated)
    failures = failures_table(payload)
    perf = perf_table(payload)
    if not official and not failures and not perf:
        return ""
    items = ""
    if official:
        items += (
            '<details class="evidence-item" id="evidence-official">'
            "<summary>What you&#8217;d pay official</summary>"
            f"<div>{official}</div></details>"
        )
    if failures:
        items += (
            '<details class="evidence-item" id="evidence-failures">'
            "<summary>Reliability &#8212; failed requests</summary>"
            f"<div>{failures}</div></details>"
        )
    if perf:
        items += (
            '<details class="evidence-item" id="evidence-perf">'
            "<summary>Main-traffic speed &#8212; ttft &amp; tps</summary>"
            f"<div>{perf}</div></details>"
        )
    return f'<div class="evidence">{items}</div>'

def official_table(payload: dict | None, catalog: dict | None,
                   dated: list | None = None) -> str:
    """Forward-looking official-price sub-table inside #pricing, or ''.

    Every number is rebuilt from the latest billed asks + the hit rate
    (design v3-final, owner-approved 2026-08-31): nothing here goes stale
    when a publisher reprices - the table moves with the prices. With
    `dated` snapshots the hit rate is the smoothed projection rate and
    thin-data routes are flagged.
    """
    if not payload or not catalog:
        return ""
    rows = official_compare.comparison_rows(payload, catalog, dated=dated)
    if not any(r["ih_eff"] is not None and r["off_eff"] is not None for r in rows):
        return ""
    routes = payload.get("routes") or {}
    body: list[str] = []
    tot_toks = 0
    tot_here = 0.0
    tot_official = 0.0
    for r in rows:
        route = html.escape(r["route"])
        if r["ih_eff"] is None or r["off_eff"] is None:
            gap = html.escape(r["note"] or "no comparison")
            body.append(
                "<tr>"
                f'<th scope="row"><code>{route}</code></th>'
                f'<td class="num" colspan="3" data-tip="No billed traffic for this route in the window — nothing to compare.">{gap}</td>'
                "</tr>"
            )
            continue
        st = routes.get(r["route"]) or {}
        toks = int(st.get("tok_in") or 0) + int(st.get("tok_out") or 0)
        tot_toks += toks
        tot_here += r["ih_eff"] * toks / 1e6
        tot_official += r["off_eff"] * toks / 1e6
        ratio = f'{r["ratio"]:g}&times;' if r["ratio"] else "n/a"
        body.append(
            "<tr>"
            f'<th scope="row"><code>{route}</code></th>'
            f'<td class="num" data-tip="What the 30-day window actually billed per M tokens (cache-discounted).">{rundata.rate_label(r["ih_eff"], fixed=4) or "n/a"}</td>'
            f'<td class="num" data-tip="The same traffic priced at official upstream list prices.">{rundata.rate_label(r["off_eff"], fixed=4) or "n/a"}</td>'
            f'<td class="num" data-tip="Official &#247; InferHub — above 1&#215; means the route bills under list price.">{ratio}</td>'
            "</tr>"
        )
    drift = [r["route"] for r in rows if r["drift"]]
    drift_note = ""
    if drift:
        names = ", ".join(f"<code>{html.escape(d)}</code>" for d in drift)
        drift_note = (
            f'<p class="section-note">Cache-rule drift on {names}: billed rows no '
            "longer match cached-input = 10% of the input ask &#8212; the rates "
            "below may be off until the next sweep confirms the rule.</p>"
        )
    soft = [
        r["route"] for r in rows
        if r.get("hit_conf") == "low" and r["ih_eff"] is not None
    ]
    soft_note = ""
    if soft and dated:
        names = ", ".join(f"<code>{html.escape(d)}</code>" for d in soft)
        soft_note = (
            f'<p class="section-note">Hit rate projected from thin data for {names}'
            " &#8212; their &#8220;here, current rates&#8221; numbers are soft until "
            "more billed traffic lands.</p>"
        )
    projection = ""
    if tot_toks and tot_here > 0:
        projection = (
            f'<p class="section-note">Rerunning this window&#8217;s workload '
            f"({rundata.token_label(tot_toks)}) at current rates: &#8776;"
            f"{rundata.cost_label(tot_here)} here vs &#8776;"
            f"{rundata.cost_label(tot_official)} at official rates "
            f"&#8212; official costs {tot_official / tot_here:.1f}&times; more.</p>"
        )
    caption = (
        "Forward-looking comparison, rebuilt every sweep from each route&#8217;s "
        "latest billed ask and the window&#8217;s cache-hit rate. Official side "
        "prices the same token mix at the upstream&#8217;s official rates; where "
        "the upstream supports cache, official cached input is assumed at 10% "
        "of list (the only rule verified on this gateway) and undiscounted "
        "otherwise &#8212; real official hit prices can be lower, so official "
        "costs are, if anything, overstated."
    )
    return (
        drift_note
        + soft_note
        + '<div class="scroll"><table class="pricing">'
        f"<caption>{caption}</caption>"
        "<thead><tr>"
        '<th scope="col">Route</th>'
        '<th scope="col" class="num">here, current rates $/M</th>'
        '<th scope="col" class="num">official $/M, same workload</th>'
        '<th scope="col" class="num">official costs</th>'
        "</tr></thead>"
        f"<tbody>{''.join(body)}</tbody>"
        "</table></div>"
        + projection
    )

def failures_table(payload: dict | None) -> str:
    """Reliability sub-table inside #pricing, or ''.

    Counts failed rows straight from the same usage-log window the price
    board uses: status="failed" rows carry no tokens and zero cost, so
    they never touch the price math — this table is where they surface.
    Renders only when the payload carries a failures block.
    """
    if not payload:
        return ""
    failures = payload.get("failures") or {}
    total = int(failures.get("total") or 0)
    failed = int(failures.get("failed") or 0)
    if not total:
        return ""
    span = html.escape(str(payload.get("range") or "30d"))
    codes = failures.get("codes") or {}
    code_line = ", ".join(f"{html.escape(c)}&#215;{n}" for c, n in codes.items()) or "&#8212;"
    rate = failures.get("rate_pct")
    headline = (
        f'<p class="section-note"><span class="chip {"bad" if failed else "ok"}">'
        f"{failed}&#8202;/&#8202;{total} failed</span> ({rate:g}% of requests) "
        f"over the {span} window &#8212; codes: {code_line}. Failed rows carry "
        "no tokens and no cost; the traffic column on the board counts attempts.</p>"
    )
    by_model = failures.get("by_model") or {}
    body: list[str] = []
    for model, m in by_model.items():
        m_failed = int(m.get("failed") or 0)
        m_reqs = int(m.get("reqs") or 0)
        m_rate = m_failed / m_reqs * 100 if m_reqs else 0.0
        rate_cell = f"{m_rate:.1f}%"
        if m_failed:
            rate_cell = f'<span class="chip bad">{rate_cell}</span>'
        m_codes = ", ".join(
            f"{html.escape(c)}&#215;{n}" for c, n in (m.get("codes") or {}).items()
        )
        body.append(
            "<tr>"
            f'<th scope="row"><code>{html.escape(model)}</code></th>'
            f'<td class="num" data-tip="Requests billed for this model in the failure window.">{m_reqs}</td>'
            f'<td class="num" data-tip="Billed requests that came back with a failure status in the window.">{m_failed}</td>'
            f'<td class="num" data-tip="Failed requests as a share of billed requests — the route&#8217;s miss rate.">{rate_cell}</td>'
            f'<td class="num" data-tip="HTTP status codes behind the failures, with counts.">{html.escape(m_codes) or "&#8212;"}</td>'
            "</tr>"
        )
    caption = (
        "Reliability over the same window: requests the gateway accepted but "
        "the upstream dropped, by route. 502 = upstream gateway failure, 429 = "
        "rate limited, 400 = rejected request."
    )
    return (
        headline
        + '<div class="scroll"><table class="pricing">'
        f"<caption>{caption}</caption>"
        "<thead><tr>"
        '<th scope="col">Route</th>'
        '<th scope="col" class="num">attempts</th>'
        '<th scope="col" class="num">failed</th>'
        '<th scope="col" class="num">fail %</th>'
        '<th scope="col" class="num">codes</th>'
        "</tr></thead>"
        f"<tbody>{''.join(body)}</tbody>"
        "</table></div>"
    )

def _iq_value(route: str, eff: float | None, intel: dict | None) -> tuple[str, str] | None:
    """(IQ label, IQ-per-$ label), or None without an intelligence snapshot.

    Em-dashes mark an unmapped route or a missing basis."""
    if not intel:
        return None
    slug = rundata.aa_slug(route)
    entry = (intel.get("models") or {}).get(slug) if slug else None
    iq = entry.get("iq") if entry else None
    if iq is None:
        return ("&#8212;", "&#8212;")
    iq_per_dollar = f"{iq / eff:,.0f}" if eff else "&#8212;"
    return (f"{iq:.1f}", iq_per_dollar)

def _iq_cells(route: str, eff: float | None, intel: dict | None) -> str:
    """IQ and IQ-per-$ cells; em-dashes when no snapshot or unmapped route."""
    values = _iq_value(route, eff, intel)
    if values is None:
        return '<td class="num"></td>' * 2
    iq, iq_per_dollar = values
    tip_iq = "Artificial Analysis intelligence index for this model (higher is smarter)."
    tip_iqd = "Intelligence per ranking dollar — index divided by the route&#8217;s $/M basis."
    return (
        f'<td class="num" data-tip="{tip_iq}">{iq}</td>'
        f'<td class="num" data-tip="{tip_iqd}">{iq_per_dollar}</td>'
    )

def _proj_eff(dated: list, payload: dict, route: str) -> float | None:
    """The forward-looking $/M for one route — delegates to probe.basis
    (P1a single money-basis owner). None without hit evidence or asks."""
    return basis.projected(payload, route, dated)

