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
from chrome import _viz_cell, section_title
from spend import DELTA_TIP, _ask_spark, ask_delta_bits, spend_block

from probe import basis, official_compare, pricing
from probe.registry import repo_root

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

def _plumb_chip() -> str:
    """The collapsed 'more' chip in its own route-row cell.

    Owner 2026-09-07 21:xxZ: the chip leaves the IQ-per-$ cell — it gets a
    dedicated last cell of the route row ('more' must not share the iq/$
    line). On open the summary word swaps to 'less' (pure CSS, the '+'
    mark is gone), and the plumbing opens as a SEPARATE sibling
    tr.plumb-row revealed by CSS :has()
    (tr:has(details[open]) + tr.plumb-row). Zero JS."""
    return (
        '<td class="plumb-cell"><details class="plumb">'
        '<summary aria-label="Show plumbing details"><span class="plumb-word">more</span></summary>'
        "</details></td>"
    )

def _plumb_row(cells: list[tuple[str, str]]) -> str:
    """The hidden sibling row that carries the plumbing grid.

    Emitted directly after each route row; CSS keeps it collapsed until the
    route row's plumb details opens. Keys arrive pre-rendered. The
    failures dd wraps (owner 2026-09-07: code lists were clipped, not
    wrapped) — .plumb-fail overrides the nowrap."""
    pairs = "".join(f"<div><dt>{k}</dt><dd>{v}</dd></div>" for k, v in cells)
    return (
        '<tr class="plumb-row"><td colspan="5">'
        f'<dl class="plumb">{pairs}</dl></td></tr>'
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
    return (f"marginal $/M <span data-tip=\"{marg_tip}\">&#9432;</span>", marg_label)

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
        "cache hit, failures, window traffic and cost, ttft p50 and tps "
        "mean (main-traffic speed, newest 24h), IQ, and retries (when a "
        "sweep replayed a route). "
        "Marginal $/M is billed cost over requests since the previous daily "
        "snapshot, dimmed when the route&#8217;s only fresh traffic is sweep "
        "probes (real money, unrepresentative workload). "
        "* = floor ask &#8212; catalog minimum, shown when the route has no billed traffic in the window. "
        "IQ = Artificial Analysis Intelligence Index (composite of 9 public evals, "
        "artificialanalysis.ai, effort level max), refreshed every sweep; IQ per $ divides it by the route&#8217;s effective $/M &#8212; higher is smarter per dollar. "
        "Sparkline bars are log-scaled &#36;0.001&#8211;&#36;10 per day. "
        "Rates cover this board&#8217;s routes, the current candidates, and every route with billed usage in the window."
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
        proj = basis.projected(payload, str(row["route"]), dated)
        ranking_basis = _board_basis(row, dated, payload, use_proj)
        eff_label = rundata.rate_label(row.get("eff_per_mtok"), fixed=4) or "n/a"
        proj_label = rundata.rate_label(proj, fixed=4) if proj is not None else None
        pair, data_label, data_tip = _pair_cell(eff_label, proj_label, use_proj)
        iq = _iq_value(str(row["route"]), ranking_basis, intel)
        series = rundata.ask_series(dated, str(row["route"]), payload)
        plumb_cells = [
            ("&#916; ask in / out",
             (f'<span data-tip="{DELTA_TIP}">'
             f'{ask_delta_bits(payload, prior, str(row["route"]))}</span>')),
            ("ask source",
             '<span data-tip="Where the ask price comes from: billed ask is the '
             'rate actually charged on this route&#8217;s fresh traffic; floor '
             'ask (*) is the catalog minimum shown when a route has no billed '
             'traffic yet.">billed ask</span>' if logged else
             '<span data-tip="Where the ask price comes from: billed ask is the '
             'rate actually charged on this route&#8217;s fresh traffic; floor '
             'ask (*) is the catalog minimum shown when a route has no billed '
             'traffic yet.">floor ask</span>'),
            ("cache hit",
             (f'<span data-tip="Share of prompt tokens served from the provider&#8217;s '
             f'prompt cache in the window &#8212; cached tokens are billed at the '
             f'cache discount, so high cache hit = cheaper effective rate.">'
             f'{rundata.cache_label(row.get("cache_pct")) or "n/a"}</span>')),
            ("failures",
             (f'<span class="plumb-fail" data-tip="Failed requests / total requests, with HTTP status '
             f'counts. Failures carry no tokens and no cost — the gateway '
             f'accepted the request and the upstream dropped it.">'
             f'{_route_failures(payload, str(row["route"]), reqs)}</span>')),
            (f"{span} traffic",
             (f'<span data-tip="Requests and tokens billed on this route over the '
             f'{span} window (failed requests excluded from tokens).">'
             f"{reqs} req · {toks} tok</span>")),
            (f"{span} cost",
             (f'<span data-tip="Total billed cost on this route over the {span} '
             f'window.">{rundata.cost_label(row.get("cost_usdc")) or "n/a"}</span>')),
        ]
        spark = _ask_spark(series)
        if spark:
            plumb_cells.append(("ask history",
             f'<span data-tip="Ask $/M history from committed probes: in/out on '
             f'the first and latest day, sparkline in between."> {spark}</span>'
             .replace("> <svg", "><svg")))
        if iq:
            plumb_cells.append(("IQ",
             (f'<span data-tip="Artificial Analysis Intelligence Index for this '
             f'route (composite of 9 public evals, artificialanalysis.ai, '
             f'effort max) &#8212; quality independent of price.">{iq[0]}</span>')))
        if retries := _route_retries(runs, str(row["route"])):
            plumb_cells.append(("retries",
             (f'<span data-tip="Sweep evidence: the first probe attempt failed and '
             f'a replay was needed. Recovered = second attempt passed; still '
             f'down = failed again.">{retries}</span>')))
        if (marg := _marginal_cell(row, runs)) is not None:
            plumb_cells.append(marg)
        # Owner 2026-09-07 20:16Z: ttft/tps live in plumbing now (the
        # separate main-traffic speed table is gone). Per-MODEL aggregates
        # from the perf block; route code maps to its model key directly.
        pm = ((payload.get("perf") or {}).get("models") or {}).get(
            str(row["route"])
        ) or {}
        ttft, tps = pm.get("ttft_p50_ms"), pm.get("tps_mean")
        ttft_label, tps_label = rundata.ttft_tps_labels(ttft, tps)
        plumb_cells.extend([
            ("ttft p50",
             (f'<span data-tip="Median time to first token across this model&#8217;s '
             f'billed requests in the newest '
             f'{int((payload.get("perf") or {}).get("window_hours") or 24)}h '
             f'window (p50) &#8212; production traffic included.">{ttft_label}</span>')),
            ("tps mean",
             (f'<span data-tip="Mean tokens/sec of generation time only (duration '
             f'minus ttft); rows with a &lt;2s generation window or impossible '
             f'tps are excluded.">{tps_label}</span>')),
        ])
        plumb_cells_row = _plumb_row(plumb_cells)
        chip = _plumb_chip()
        # Owner 2026-09-07 21:xxZ: 'more' gets its own last cell — it must
        # not share the iq/$ line. The plumbing grid opens as a SEPARATE
        # full-width sibling <tr> right below (CSS :has(), zero JS).
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
            + chip
            + "</tr>"
            + plumb_cells_row
        )
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
            + spend_block(payload, runs)
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
        f'<span class="lg"><i class="dot dot-ok"></i> in use &#8212; {IN_USE_MIN_REQS}+ reqs/24h</span>'
        '<span class="lg"><i class="dot dot-warn"></i> probes only</span>'
        '<span class="lg"><i class="dot dot-mut"></i> little/no traffic</span>'
        '<span class="lg lg-size"><i class="dot dot-mut dot-s"></i><i class="dot dot-mut dot-l"></i>'
        " size = 24h traffic</span>"
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
    runs = runs or []
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
                label_step: float, label_cap: int, cls: str,
                char_w: float) -> str:
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
                    f'<text x="{pad_l - 4}" y="{gy + 3:.1f}" class="st" text-anchor="start">{v}</text>'
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
            # Frame clamp (browser QA 2026-09-08): the heuristic above flips
            # almost every mobile label left, and near the left frame edge
            # the text bled up to 54px off the canvas. If the chosen side
            # pushes the text outside the viewBox, use the other side.
            label_right = cx < W - pad_r - label_cap * char_w
            if label_right and cx + 10 + len(short) * char_w > W - 2:
                label_right = False
            elif not label_right and cx - 10 - len(short) * char_w < 2:
                label_right = True
            lx = cx + 10 if label_right else cx - 10
            anchor = "start" if label_right else "end"
            x0 = (cx + 10) if label_right else (cx - 10 - len(short) * char_w)
            x1 = x0 + len(short) * char_w

            def _hits_dot(y: float, _cx=cx, _cy=cy, _x0=x0, _x1=x1) -> bool:
                # Dot obstructed when its center is inside the glyph band
                # and its center+r crosses THIS label's span (x0..x1).
                # Own dot excluded by exact coords (it always borders the
                # label). Loop vars bound at def time (B023) — the closures
                # are defined and called within this same iteration.
                for ox, oy, orr in dot_obs:
                    if ox == _cx and oy == _cy:
                        continue
                    # Glyph band around the baseline: ascenders ~9px above,
                    # descenders ~3px below (11px label font).
                    if not (y - 9 - orr <= oy <= y + 3 + orr):
                        continue
                    if ox + orr >= _x0 and ox - orr <= _x1:
                        return True
                return False

            def _row_taken(y: float, _x0=x0, _x1=x1) -> bool:
                # A previously placed label blocks this row only when the
                # baselines are close AND the x-spans overlap (same baseline
                # at opposite chart ends does NOT collide).
                return any(
                    abs(y - oy) < label_step * 0.9 and ox0 < _x1 and ox1 > _x0
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
                x0 = (cx + 10) if label_right else (cx - 10 - len(short) * char_w)
                x1 = x0 + len(short) * char_w
                ly = cy + 3
                # Both sides blocked at the dot: give up, park at dot — but
                # keep the frame clamp (browser QA 2026-09-08): never park a
                # left-anchored label past the left frame edge.
                if (
                    (_row_taken(ly) or _hits_dot(ly))
                    and not label_right
                    and cx - 10 - len(short) * char_w < 2
                ):
                    label_right = True
                    lx = cx + 10
                    anchor = "start"
                    x0 = cx + 10
                    x1 = x0 + len(short) * char_w
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
        + _render(640, 300, 44, 12, 14, 34, 13.0, 40, "desktop", 5.7)
        # Mobile (QA M2): portrait, larger relative type, full route names
        # (owner 2026-09-08: "labels not truncated" — cap raised to 35, the
        # longest probed route; char_w matches the 14px mobile label font).
        + _render(440, 560, 12, 12, 20, 36, 17.0, 35, "mobile", 8.4)
        + _scatter_legend()
        + "</figure>"
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

