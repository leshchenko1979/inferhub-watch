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

    Owner 2026-09-07 ("do we need show plumbing on separate lines? why not
    a small 'more' chip with an expansion mark?"): the summary is a compact
    right-aligned 'more +' chip, not a full-width text line; the collapsed
    row shrinks to chip height. A <details> can't toggle a DIFFERENT table
    row without JS, so the chip lives on the plumbing row itself.

    Keys arrive pre-rendered (callers escape any data-derived text)."""
    pairs = "".join(f"<div><dt>{k}</dt><dd>{v}</dd></div>" for k, v in cells)
    return (
        f'<tr class="plumb-row"><td colspan="{colspan}"><details>'
        '<summary aria-label="Show plumbing details"><span class="plumb-word">more</span>'
        ' <span class="plumb-mark" aria-hidden="true">+</span></summary>'
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
        "Effective bars are log-scaled from the cheapest to the priciest "
        "route on the board (priciest = full bar) and colored teal &#8804; "
        "&#36;0.02, amber above. "
        "Show plumbing folds each route&#8217;s ask movement "
        "(the color key sits under the table), ask source, ask history, "
        "cache hit, failures, window traffic and cost, IQ, and retries (when a "
        "sweep replayed a route). "
        "Marginal $/M is billed cost over requests since the previous daily "
        "snapshot, dimmed when the route&#8217;s only fresh traffic is sweep "
        "probes (real money, unrepresentative workload). "
        "* = floor ask &#8212; catalog minimum, shown when the route has no billed traffic in the window. "
        "IQ = Artificial Analysis Intelligence Index (composite of 9 public evals, "
        "artificialanalysis.ai, effort level max), refreshed every sweep; IQ per $ divides it by the route&#8217;s effective $/M &#8212; higher is smarter per dollar. "
        "Sparkline bars are log-scaled &#36;0.001&#8211;&#36;10 per day. "
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
    # Bar scale anchored to the board's own price range (owner 2026-09-07:
    # "why is the line under prices just 50% on the most expensive model?" —
    # the old fixed $0.001–$10 log scale left 4 decades of dead headroom).
    # Log min→max of the routes' ranking prices; priciest board route = 100%.
    board_prices = [
        v for v in (_board_basis(r, dated, payload, use_proj) for r in rows)
        if isinstance(v, (int, float)) and v > 0
    ]
    bar_lo = min(board_prices) if board_prices else 0.001
    bar_hi = max(board_prices) if board_prices else 10.0
    body_rows = []
    for row in rows:
        logged = row.get("source") == "usage-logs"
        mark = "" if logged else '<span class="ask-mark" title="floor ask &#8212; catalog minimum, no billed traffic yet">*</span>'
        cand = (' <span class="cand-mark" title="&#9666; cand = radar candidate: '
                'the nightly market scan flagged this route as a cheaper/stronger '
                'alternative to its family incumbent, and the sweep probes it '
                'alongside the board. How it is given: the scan ranks catalog '
                'routes by predicted saving vs its family (min 20% predicted '
                'saving, IQ within 10% of incumbent), top-2 per family enter the '
                'sweep; routes that keep passing all checks get billed here too. '
                'Full method: candidates section.">&#9666; cand</span>') if row.get("candidate") else ""
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
                rundata.log_bar_pct(ranking_basis, bar_lo, bar_hi),
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
            ("ask source",
             f'<span title="Where the ask price comes from: billed ask is the '
             f'rate actually charged on this route&#8217;s fresh traffic; floor '
             f'ask (*) is the catalog minimum shown when a route has no billed '
             f'traffic yet.">billed ask</span>' if logged else
             f'<span title="Where the ask price comes from: billed ask is the '
             f'rate actually charged on this route&#8217;s fresh traffic; floor '
             f'ask (*) is the catalog minimum shown when a route has no billed '
             f'traffic yet.">floor ask</span>'),
            ("cache hit",
             f'<span title="Share of prompt tokens served from the provider&#8217;s '
             f'prompt cache in the window &#8212; cached tokens are billed at the '
             f'cache discount, so high cache hit = cheaper effective rate.">'
             f'{rundata.cache_label(row.get("cache_pct")) or "n/a"}</span>'),
            ("failures",
             f'<span title="Failed requests / total requests, with HTTP status '
             f'counts. Failures carry no tokens and no cost — the gateway '
             f'accepted the request and the upstream dropped it.">'
             f'{_route_failures(payload, str(row["route"]), reqs)}</span>'),
            (f"{span} traffic",
             f'<span title="Requests and tokens billed on this route over the '
             f'{span} window (failed requests excluded from tokens).">'
             f"{reqs} req · {toks} tok</span>"),
            (f"{span} cost",
             f'<span title="Total billed cost on this route over the {span} '
             f'window.">{rundata.cost_label(row.get("cost_usdc")) or "n/a"}</span>'),
        ]
        spark = _ask_spark(series)
        if spark:
            plumb_cells.append(("ask history", spark))
        if iq:
            plumb_cells.append(("IQ",
             f'<span title="Artificial Analysis Intelligence Index for this '
             f'route (composite of 9 public evals, artificialanalysis.ai, '
             f'effort max) &#8212; quality independent of price.">{iq[0]}</span>'))
        if retries := _route_retries(runs, str(row["route"])):
            plumb_cells.append(("retries",
             f'<span title="Sweep evidence: the first probe attempt failed and '
             f'a replay was needed. Recovered = second attempt passed; still '
             f'down = failed again.">{retries}</span>'))
        if (marg := _marginal_cell(row, runs)) is not None:
            plumb_cells.append(marg)
        body_rows.append(_plumb_row(plumb_cells))
    caption = _pricing_caption(span, use_proj, gate)
    # Delta legend as a visible note (QA: it lived only in a hover title).
    legend = (
        '<p class="section-note delta-legend">&#916; ask in / out vs the previous '
        "daily snapshot: <span class=\"delta-down\">&#8595; cheaper</span> · "
        "<span class=\"delta-up\">&#8593; pricier</span> · "
        "&#8212; no earlier snapshot. In = prompt tokens, out = completion tokens.</p>"
    )
    from tmpl import render

    return render(
        "pricing.html",
        title=html.escape(section_title("pricing")),
        note=html.escape(note),
        caption=caption,
        body_rows="".join(body_rows),
        after=(
            legend
            + scatter_section(payload, intel, runs)
            + spend_block(payload, runs)
            + evidence_block(payload, rundata.load_catalog(ROOT), dated)
        ),
    )

IN_USE_MIN_REQS = 100  # 24h billed reqs to count as "in use" — 2026-09-07 live
# data: real traffic starts at 457 req/24h while probe-noise tops out at 13;
# anything in between is trace traffic, drawn gray so teal stays meaningful.

def usage_color(reqs: int | None, probe_only: bool = False) -> str:
    """Point color (owner orders 2026-09-07): teal = in use (real billed
    traffic in the window at meaningful volume — probe-only routes do NOT
    count), amber = probes only, gray = little or no traffic (trace-level
    billed requests are NOT "in use": the owner called the old 1-req
    threshold out as flooding the category)."""
    if probe_only:
        return "var(--warn, #c90)"
    if not reqs or reqs < IN_USE_MIN_REQS:
        return "var(--muted)"
    return "var(--ok)"

def usage_radius(reqs: int | None, probe_only: bool = False) -> float:
    """Dot size tracks usage (owner order 2026-09-07): sqrt-scaled 3.5→9px
    over 0..10k reqs/24h so the 8.9k-req leader reads big without
    flattening the single-digit tail. Probe-only dots stay small regardless
    — their request count is sweep noise, not usage."""
    if not reqs or reqs <= 0 or probe_only:
        return 3.5
    return 3.5 + 5.5 * math.sqrt(min(reqs, 10000) / 10000.0)

def _scatter_legend() -> str:
    return (
        '<div class="scatter-legend">'
        '<span><svg width="12" height="12"><circle cx="6" cy="6" r="5" fill="var(--ok)"/></svg>'
        " in use (&#8805;100 reqs/24h)</span>"
        '<span><svg width="12" height="12"><circle cx="6" cy="6" r="5" fill="var(--warn, #c90)"/></svg>'
        " probes only</span>"
        '<span><svg width="12" height="12"><circle cx="6" cy="6" r="5" fill="var(--muted)"/></svg>'
        " little/no traffic</span>"
        '<span class="legend-size"><svg width="34" height="12">'
        '<circle cx="6" cy="6" r="3.5" fill="var(--muted)"/>'
        '<circle cx="20" cy="6" r="6" fill="var(--muted)"/>'
        "</svg> dot size = 24h requests</span>"
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
    # Rendered TWICE (QA M2: a fixed viewBox scales SVG text to illegibility on
    # phones — 5.6px labels at 390px): a 640x300 landscape SVG for
    # desktop and a 440x520 portrait SVG with larger baked-in type and
    # truncated labels for mobile; CSS shows exactly one per viewport.
    def _render(W: int, H: int, pad_l: int, pad_r: int, pad_t: int, pad_b: int,
                label_step: float, label_cap: int, cls: str) -> str:
        def sx(eff: float) -> float:
            # Cheaper = RIGHT (board money law + caption): invert so
            # higher price renders left. x_lo (cheap) sits at the right edge.
            lo, hi = math.log10(x_lo), math.log10(x_hi)
            return pad_l + (hi - math.log10(eff)) / (hi - lo) * (W - pad_l - pad_r)

        def sy(iq: float) -> float:
            return pad_t + (1 - (iq - y_lo) / (y_hi - y_lo)) * (H - pad_t - pad_b)

        log_lo, log_hi = math.log10(x_lo), math.log10(x_hi)
        grid = []
        step = (log_hi - log_lo) / 4
        # Gridline ticks walk cheap→expensive left→right (axis inverted);
        # edge ticks anchor inward so the outermost label can't clip (QA m10).
        for i in range(5):
            v = 10 ** (log_lo + i * step)
            gx = W - pad_r - i * (W - pad_l - pad_r) / 4
            if i == 0:
                anchor = "end"
            elif i == 4:
                anchor = "start"
            else:
                anchor = "middle"
            grid.append(
                f'<line x1="{gx:.1f}" y1="{pad_t}" x2="{gx:.1f}" y2="{H - pad_b}" class="sg"/>'
                f'<text x="{gx:.1f}" y="{H - pad_b + 14}" class="st" text-anchor="{anchor}">'
                f"{rundata.rate_label(v, fixed=4)}</text>"
            )
        # IQ gridlines at nice steps of 10.
        iq_lo, iq_hi = int(math.floor(y_lo / 10) * 10), int(math.ceil(y_hi / 10) * 10)
        for v in range(iq_lo, iq_hi + 1, 10):
            gy = sy(v)
            if pad_t <= gy <= H - pad_b:
                grid.append(
                    f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{W - pad_r}" y2="{gy:.1f}" class="sg"/>'
                    f'<text x="{pad_l - 6}" y="{gy + 3:.1f}" class="st" text-anchor="end">{v}</text>'
                )
        dots = ""
        # Label-collision pass (QA finding: cx/cb pairs at identical y printed
        # on top of each other). Track occupied y rows; nudge a colliding label
        # down/up by rows of label_step px until free, both sides, so every
        # name reads. A thin leader line ties the moved label back to its dot
        # (owner 2026-09-07: "labels torn off from the dots") — and when the
        # nudge gives up, the label sits at the dot instead of wandering.
        # Owner follow-up: labels must not obstruct OTHER dots either — every
        # dot is an obstacle too (y within label band AND x inside the text
        # span), not just other labels.
        # Owner follow-up: row-blocking must be x-aware too — two labels on
        # the same baseline but at opposite ends of the chart do NOT collide
        # (the old y-only model pushed the rightmost label rows away "for no
        # reason"). Track occupied (baseline, x0, x1) spans; a row is taken
        # only when baselines are close AND x-spans overlap.
        occupied: list[tuple[float, float, float]] = []
        dot_obs = [
            (sx(eff), sy(iq), usage_radius(reqs, ponly))
            for _route, eff, iq, reqs, _bt, ponly in points
        ]
        for route, eff, iq, reqs, basis_tag, ponly in points:
            cx, cy = sx(eff), sy(iq)
            r = usage_radius(reqs, ponly)
            name = html.escape(route)
            short = name if len(name) <= label_cap else name[: label_cap - 1] + "…"
            # Label right of the dot; flip left when it would clip the frame.
            label_right = cx < W - pad_r - label_cap * 6
            lx = cx + 10 if label_right else cx - 10
            anchor = "start" if label_right else "end"
            x0 = (cx + 10) if label_right else (cx - 10 - len(short) * 6)
            x1 = x0 + len(short) * 6

            def _hits_dot(y: float) -> bool:
                # Dot obstructed when its center is inside the glyph band
                # and its center+r crosses THIS label's span (x0..x1).
                # Own dot excluded by exact coords (it always borders the
                # label).
                for ox, oy, orr in dot_obs:
                    if ox == cx and oy == cy:
                        continue
                    # Glyph band around the baseline: ascenders ~9px above,
                    # descenders ~3px below (11px label font).
                    if not (y - 9 - orr <= oy <= y + 3 + orr):
                        continue
                    if ox + orr >= x0 and ox - orr <= x1:
                        return True
                return False

            def _row_taken(y: float) -> bool:
                # A previously placed label blocks this row only when the
                # baselines are close AND the x-spans overlap (same baseline
                # at opposite chart ends does NOT collide).
                return any(
                    abs(y - oy) < label_step * 0.9 and ox0 < x1 and ox1 > x0
                    for oy, ox0, ox1 in occupied
                )

            ly = cy + 3
            leader = ""
            guard = 0
            # Nearest-first probe ladder: down 1, up 1, down 2, up 2 ... so a
            # label always takes the CLOSEST free row to its dot (owner
            # 2026-09-07: "label needs to be as close to their dot as
            # possible" — the old ladder skipped the up-1 row entirely).
            # "Free" = no overlapping label span AND no foreign dot inside
            # the glyph band/span.
            while (_row_taken(ly) or _hits_dot(ly)) and guard < 10:
                guard += 1
                ly = cy + 3 + ((guard + 1) // 2) * label_step * (1 if guard % 2 else -1)
                if ly < pad_t + 8 or ly > H - pad_b - 4:
                    ly = cy + 3  # reset and try the other direction next round
            if guard >= 10 and (_row_taken(ly) or _hits_dot(ly)):
                # Ladder exhausted on this side: flip to the opposite side of
                # the dot before accepting any collision (the other side is
                # usually empty — it was only not tried when the edge forced
                # the original side).
                label_right = not label_right
                lx = cx + 10 if label_right else cx - 10
                anchor = "start" if label_right else "end"
                x0 = (cx + 10) if label_right else (cx - 10 - len(short) * 6)
                x1 = x0 + len(short) * 6
                ly = cy + 3
                if _row_taken(ly) or _hits_dot(ly):
                    # Both sides blocked at the dot: give up, park at dot.
                    ly = cy + 3
            if abs(ly - (cy + 3)) > 1:
                # Label moved: draw a leader from the dot edge to the label.
                y1 = cy + (r + 1) if ly > cy else cy - (r + 1)
                leader = (
                    f'<line x1="{cx:.1f}" y1="{y1:.1f}" x2="{lx:.1f}" y2="{ly - 3:.1f}" '
                    'class="sleader"/>'
                )
            occupied.append((ly, x0, x1))
            tip = (
                f"{html.escape(route)} &#8212; {rundata.rate_label(eff, fixed=4)} $/M ({basis_tag}) &#183; "
                f"IQ {iq:.1f} &#183; {reqs} reqs/{hours}h"
                + (" &#183; probes only" if ponly else "")
            )
            dots += (
                f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="{usage_color(reqs, ponly)}" '
                f'data-tip="{tip}" '
                f'aria-label="{html.escape(route)}"/>'
                f"{leader}"
                f'<text x="{lx:.1f}" y="{ly:.1f}" class="slabel" text-anchor="{anchor}" '
                f'data-tip="{tip}">{short}</text>'
            )
        return (
            f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{html.escape(caption)}" class="{cls}">'
            f"{''.join(grid)}{dots}"
            '<text x="50%" y="12" class="st" text-anchor="middle">AA IQ</text>'
            "</svg>"
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
        # Desktop: landscape, full route names (640x300).
        + _render(640, 300, 44, 12, 14, 34, 13.0, 40, "desktop")
        # Mobile (QA M2): portrait, larger relative type, truncated names.
        # At a 358px viewport the 440 viewBox scales ~0.81, so 14px labels
        # land at ~11px rendered — legible.
        + _render(440, 520, 40, 96, 20, 36, 17.0, 16, "mobile")
        + _scatter_legend()
        + "</figure>"
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
            f'<td class="num" data-tip="Official &#247; InferHub — above 1× means the route bills under list price.">{ratio}</td>'
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
    code_line = ", ".join(f"{html.escape(c)}×{n}" for c, n in codes.items()) or "&#8212;"
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
            f"{html.escape(c)}×{n}" for c, n in (m.get("codes") or {}).items()
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
