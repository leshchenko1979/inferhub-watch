from __future__ import annotations

import html
import os
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_SITE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
if str(_SITE) not in sys.path:
    sys.path.insert(0, str(_SITE))

import mdhtml  # noqa: E402
import rundata  # noqa: E402
import tmpl  # noqa: E402
from probe.publishers import publisher_label  # noqa: E402
from probe.registry import load_aliases, load_registry  # noqa: E402

from probe import market, radar, official_compare, pricing  # noqa: E402

GITHUB = "https://github.com/leshchenko1979/inferhub-watch"
CLONE = (
    f'Clone <a href="{GITHUB}">leshchenko1979/inferhub-watch</a>, '
    "set <code>INFERHUB_API_KEY</code>, run <code>python3 -m probe.run</code>."
)
FONTS = (
    "https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500"
    "&family=IBM+Plex+Sans:wght@400;500;600&display=swap"
)
SECTIONS = (
    ("pricing", "Cost per M tokens"),
    ("results", "Probe results"),
    ("earlier", "Past runs"),
    ("method", "How we test"),
)


def base_href() -> str:
    raw = os.environ.get("PAGES_BASE", "/inferhub-watch").rstrip("/")
    return raw or ""


def load_runs() -> list[dict]:
    return rundata.load_runs(ROOT)


def results_available() -> bool:
    """True when the latest run renders any model group (board or audition)."""
    runs = rundata.load_runs(ROOT)
    if not runs:
        return False
    return bool(run_groups(runs[-1]))


def run_groups(run: dict) -> list[dict]:
    """Model groups [{model, routes}] from a run's sweep.

    Every probed family appears — board-only families (no audition routes)
    with an empty route list — so the section renders the in-use verdict
    even when the market shortlist found nothing cheaper.

    Cells are the primary source (order = first appearance). Routes listed
    in the run's shortlist without cells — a sweep aborted early — still
    get a row under their own family, so they render as unprobed instead
    of disappearing.
    """
    order: list[str] = []
    routes_by_model: dict[str, list[str]] = {}

    def add(model: str, alias: str) -> None:
        if not model or not alias:
            return
        if model not in routes_by_model:
            routes_by_model[model] = []
            order.append(model)
        if alias not in routes_by_model[model]:
            routes_by_model[model].append(alias)

    for cell in run.get("cells") or []:
        # Families come from EVERY probed cell — board cells included — so a
        # board-only family (no shortlist candidates this run) still renders
        # a group. Board cells in older runs carry no model key; fall back to
        # the alias's family ("ali/qwen3.8-max" -> "qwen3.8-max").
        alias = str(cell.get("alias") or "")
        model = cell.get("model") or market.family(alias)
        if model and model not in routes_by_model:
            routes_by_model[model] = []
            order.append(model)
    for cell in rundata.candidate_cells(run):
        add(cell.get("model") or "", cell.get("alias") or "")
    for route in run.get("candidates") or []:
        route = str(route)
        add(market.family(route), route)
    return [{"model": model, "routes": routes_by_model[model]} for model in order]


def alias_heading(alias: str, resolved: str) -> str:
    return (
        f'<th class="alias-cell" scope="row">'
        f'<span class="alias">{html.escape(alias)}</span>'
        f'<span class="pub">{html.escape(publisher_label(resolved))}</span>'
        "</th>"
    )


def board_nav() -> str:
    items = []
    for sid, title in SECTIONS:
        if sid == "pricing" and not rundata.load_pricing(ROOT):
            continue
        if sid == "results" and not results_available():
            continue
        items.append(
            f'<li><a href="#{html.escape(sid)}">{html.escape(title)}</a></li>'
        )
    return tmpl.render("nav.html", items="".join(items))


def section_title(section_id: str) -> str:
    return dict(SECTIONS)[section_id]


def shell(
    title: str,
    body: str,
    *,
    crumb: str = "",
    nested: bool = False,
    page_class: str = "",
    page_nav: str = "",
    header_meta: str = "",
    with_footer: bool = True,
) -> str:
    base = base_href()
    if base:
        home = f"{base}/"
        css = f"{base}/style.css"
    elif nested:
        home = "../index.html"
        css = "../style.css"
    else:
        home = "./"
        css = "style.css"
    crumb_html = (
        f'<p class="crumb"><a href="{html.escape(home)}">InferHub Watch</a> / {html.escape(crumb)}</p>'
        if crumb
        else ""
    )
    script = ""
    if page_class == "board":
        js = (_SITE / "templates" / "board.js").read_text()
        script = f"<script>\n{js}</script>"
    return tmpl.render(
        "shell.html",
        title=html.escape(title),
        fonts=FONTS,
        css=html.escape(css),
        body_class=f' class="{html.escape(page_class)}"' if page_class else "",
        home=html.escape(home),
        page_nav=page_nav,
        header_meta=header_meta,
        crumb=crumb_html,
        body=body,
        footer=f"<footer><p>{CLONE}</p></footer>" if with_footer else "",
        script=script,
    )


def _viz_cell(
    label: str,
    bar_pct: float,
    color_cls: str,
    data_label: str = "",
    data_tip: str = "",
) -> str:
    """Value on top, a 4px bar underneath (Gatus-style)."""
    bar = (
        f'<i class="{color_cls}" style="width:{bar_pct:.0f}%"></i>'
        if bar_pct > 0
        else ""
    )
    attr = f' data-label="{html.escape(data_label)}"' if data_label else ""
    attr += f' data-tip="{html.escape(data_tip)}"' if data_tip else ""
    return (
        f'<td class="num viz"{attr}><span class="viz-val">{label}</span>'
        f'<span class="viz-bar">{bar}</span></td>'
    )


# ── spend dashboard ──

SPARK_DAYS = 30
SPARK_BAR_W = 10
SPARK_GAP = 3
SPARK_BAR_H = 36  # tallest bar; log-scaled like the effective-rate bars
SPARK_LABEL_H = 14
SPARK_LO, SPARK_HI = 0.001, 10.0  # $/day domain, mirrors the $/M bar domain


def _snapshot_day(payload: dict) -> str:
    """The UTC day the snapshot speaks for; falls back to the newest day entry."""
    day = (payload.get("generated_at") or "")[:10]
    if day:
        return day
    days = rundata.spend_days(payload)
    return days[-1]["date"] if days else ""


def spend_sparkline(payload: dict) -> str:
    """Daily spend for the last 30 days as an inline SVG bar strip.

    Bars are log-scaled over $0.001–$10/day so a quiet day next to a spike
    stays visible; trafficless days render as a 2px stub. Per-bar <title>
    carries date, cost and request count — no JS anywhere.
    """
    days = rundata.spend_days(payload)
    today = _snapshot_day(payload)
    if not days or not today:
        return ""
    try:
        end = date.fromisoformat(today)
    except ValueError:
        return ""
    by_date = {d["date"]: d for d in days}
    step = SPARK_BAR_W + SPARK_GAP
    width = SPARK_DAYS * step - SPARK_GAP
    height = SPARK_BAR_H + SPARK_LABEL_H
    bars = []
    for i in range(SPARK_DAYS):
        day = (end - timedelta(days=SPARK_DAYS - 1 - i)).isoformat()
        x = i * step
        entry = by_date.get(day)
        cost = rundata.day_cost(entry) if entry else 0.0
        title = rundata.month_day_label(day) or day
        if entry and cost > 0:
            pct = rundata.log_bar_pct(cost, SPARK_LO, SPARK_HI)
            h = max(2.0, SPARK_BAR_H * pct / 100.0)
            reqs = entry.get("requests") or 0
            label = rundata.cost_label(f"{cost:.6f}") or f"${cost:.4f}"
            bars.append(
                f'<rect class="spark-bar" x="{x}" y="{SPARK_BAR_H - h:.1f}" '
                f'width="{SPARK_BAR_W}" height="{h:.1f}" rx="1">'
                f"<title>{html.escape(f'{title} · {label} · {reqs} req')}</title>"
                "</rect>"
            )
        else:
            suffix = " · no billed traffic" if entry is None else ""
            bars.append(
                f'<rect class="spark-zero" x="{x}" y="{SPARK_BAR_H - 2}" '
                f'width="{SPARK_BAR_W}" height="2" rx="1">'
                f"<title>{html.escape(title + suffix)}</title></rect>"
            )
    start_label = rundata.month_day_label((end - timedelta(days=SPARK_DAYS - 1)).isoformat())
    end_label = rundata.month_day_label(today)
    return (
        f'<svg class="spend-spark" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" role="img" '
        'aria-label="Daily spend, last 30 days">'
        + "".join(bars)
        + f'<line class="spark-axis" x1="0" y1="{SPARK_BAR_H + 0.5}" '
        f'x2="{width}" y2="{SPARK_BAR_H + 0.5}"/>'
        + f'<text class="spark-lab" x="0" y="{height - 2}">{html.escape(start_label)}</text>'
        + f'<text class="spark-lab" x="{width}" y="{height - 2}" '
        f'text-anchor="end">{html.escape(end_label)}</text>'
        "</svg>"
    )


def spend_block(payload: dict, runs: list[dict]) -> str:
    """Collapsed spend fold: MTD + today + probe share + 30-day spark.

    Rides below the board table in the evidence tier — the money trail
    is supporting detail, not the glance layer. Returns '' without data.
    """
    days = rundata.spend_days(payload)
    today = _snapshot_day(payload)
    if not days or not today:
        return ""
    mtd = rundata.spend_between(days, today[:7] + "-01", today)
    today_cost = rundata.spend_between(days, today, today)
    first_day = rundata.day_key(runs[0]) if runs else ""
    since = rundata.month_day_label(first_day)
    probe_cap = "probe runs" + (f" · since {since}" if since else "")
    mtd_label = rundata.cost_label(f"{mtd:.6f}") or "$0.00"
    stats = (
        (mtd_label, "month to date"),
        (rundata.cost_label(f"{today_cost:.6f}") or "$0.00", "today so far"),
        (rundata.cost_label(f"{rundata.probe_spend(runs):.6f}") or "$0.00", probe_cap),
    )
    bits = "".join(
        f'<div class="spend-stat"><span class="spend-val">{val}</span>'
        f'<span class="spend-cap">{html.escape(cap)}</span></div>'
        for val, cap in stats
    )
    return (
        '<details class="evidence-item spend-item" id="spend">'
        f"<summary>Spend &#8212; {mtd_label} month to date</summary>"
        f'<div><div class="spend-block"><div class="spend-stats">{bits}</div>'
        f"{spend_sparkline(payload)}</div></div></details>"
    )


def _delta_span(delta: float) -> str:
    if abs(delta) < 1e-9:
        return '<span class="delta-flat" title="ask unchanged">=</span>'
    mag = rundata.rate_label(abs(delta)) or f"${abs(delta):.3f}"
    if delta < 0:
        return f'<span class="delta-down" title="ask fell">&#8595;{mag}</span>'
    return f'<span class="delta-up" title="ask rose">&#8593;{mag}</span>'


DELTA_TIP = (
    "&#916; ask vs the previous daily snapshot: &#8595; teal cheaper, "
    "&#8593; amber pricier, &#8212; no earlier snapshot."
)


def ask_delta_bits(payload: dict | None, prior: dict | None, route: str) -> str:
    """Δ ask in / out spans: movement vs the prior snapshot, '—' without one."""
    deltas = rundata.ask_deltas(payload, prior, route)
    if deltas is None:
        return (
            '<span class="delta-flat" '
            'title="no earlier snapshot for this route">&#8212;</span>'
        )
    return (
        _delta_span(deltas["in"])
        + '<span class="delta-sep"> / </span>'
        + _delta_span(deltas["out"])
    )


def _ask_spark(points: list[tuple[str, float, float]]) -> str:
    """Little inline-SVG line graph of a route's ask history, or ''.

    Two lines on one shared scale (honest: in and out stay proportional);
    ask_in is the brighter stroke, ask_out dimmed, last point dotted.
    Needs >= 2 points; days without a logged ask are absent from `points`.
    """
    if len(points) < 2:
        return ""
    w, h, pad = 120.0, 26.0, 2.5
    n = len(points)
    hi = max(max(p[1] for p in points), max(p[2] for p in points)) or 1.0

    def polyline(vals: list[float]) -> tuple[str, str, str]:
        coords = []
        for i, v in enumerate(vals):
            x = pad + i * (w - 2 * pad) / (n - 1)
            y = h - pad - (v / hi) * (h - 2 * pad)
            coords.append((x, y))
        dots = "".join(f'<circle class="s-dot" cx="{x:.1f}" cy="{y:.1f}" r="1.5"/>' for x, y in coords)
        path = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
        lx, ly = coords[-1]
        return (f'<polyline class="s-line" points="{path}"/>', dots,
                f'<circle class="s-last" cx="{lx:.1f}" cy="{ly:.1f}" r="2"/>')

    in_path, in_dots, _ = polyline([p[1] for p in points])
    out_path, _, out_last = polyline([p[2] for p in points])
    first, last = points[0][0], points[-1][0]
    tip = " / ".join(f"{d}: {a:g}/{o:g}" for d, a, o in points)
    return (
        f'<svg class="ask-spark" width="{w:g}" height="{h:g}" viewBox="0 0 {w:g} {h:g}" '
        f'role="img" aria-label="ask history {first} to {last}" '
        f'data-tip="Ask $/M, {html.escape(tip)}">'
        f'<title>ask in/out, {html.escape(first)} &#8594; {html.escape(last)}</title>'
        f"{in_path}{in_dots}{out_path}{out_last}</svg>"
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
    return f'<td class="num">{iq}</td><td class="num">{iq_per_dollar}</td>'


def _proj_eff(dated: list, payload: dict, route: str) -> float | None:
    """The forward-looking $/M for one route: current billed asks priced
    at the smoothed projection hit rate. None without hit evidence or
    asks - callers fall back to the realized eff."""
    stats = (payload.get("routes") or {}).get(route) or {}
    hit, _conf = official_compare.projection_hit(
        dated, route, stats, payload.get("routes") or {}
    )
    return official_compare.inferhub_eff(stats, hit=hit)


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
            label = rundata.rate_label(proj) or "n/a"
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


def _probe_only(row: dict, runs: list[dict]) -> bool:
    """True when every marginal request for this route falls inside a sweep
    window that probed it — the route's only fresh traffic is probes, so
    the marginal $/M is real money but an unrepresentative (cache-cold,
    tiny-prompt) workload. Needs the full ts list: a truncated list (or a
    single ts outside every window) means working traffic exists.

    Timestamps are compared as datetimes, never as strings: usage-log
    stamps end in Z while run-window stamps end in +00:00, and lexical
    order between the two formats is wrong (Z sorts above '+')."""
    reqs = int(row.get("marginal_reqs") or 0)
    ts_list = row.get("marginal_ts") or []
    if not reqs or row.get("marginal_ts_truncated") or len(ts_list) < reqs:
        return False
    windows = []
    for run in runs:
        start = pricing._parse_ts(run.get("started_at"))
        end = pricing._parse_ts(run.get("finished_at"))
        if start and end:
            windows.append((start, end, set(run.get("aliases") or [])))
    if not windows:
        return False
    route = str(row.get("route") or "")
    for ts in ts_list:
        parsed = pricing._parse_ts(ts)
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
    stats = (payload.get("failures") or payload.get("errors") or {}).get("by_model") or {}  # noqa: legacy key until the 2026-09-02 sweep writes "failures"
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
    # The basis the board ranks on: realized 30d eff, or the projection
    # once the backtest gate says the forward view predicts the next
    # snapshot within tolerance (projection_gate - recomputed per render).
    gate = official_compare.projection_gate(dated)
    use_proj = bool(gate.get("pass"))

    def _basis(row: dict) -> float | None:
        if use_proj:
            proj = _proj_eff(dated, payload, str(row["route"]))
            if proj is not None:
                return proj
        return row.get("eff_per_mtok")

    # Board order: IQ per $ descending (ontology: the smarter-per-dollar verdict
    # is the point of the board). Routes without an IQ mapping or eff sink last.
    def _iq_per_dollar(row: dict) -> float:
        slug = rundata.aa_slug(str(row["route"]))
        entry = (intel.get("models") or {}).get(slug) if slug else None
        iq = entry.get("iq") if entry else None
        eff = _basis(row)
        try:
            return iq / eff if iq is not None and eff else float("-inf")
        except ZeroDivisionError:
            return float("-inf")

    rows.sort(key=_iq_per_dollar, reverse=True)
    body_rows = []
    for row in rows:
        logged = row.get("source") == "usage-logs"
        mark = "" if logged else '<span class="ask-mark" title="floor ask &#8212; catalog minimum, no billed traffic yet">*</span>'
        ask_in = rundata.rate_label(row.get("ask_in")) or "n/a"
        ask_out = rundata.rate_label(row.get("ask_out")) or "n/a"
        reqs = int(row.get("reqs") or 0)
        toks = rundata.token_label(
            int(row.get("tok_in") or 0) + int(row.get("tok_out") or 0)
        )
        # Decision row: two figures a scanner reads — the rate pair and
        # IQ per $. Everything else (ask movement, history, cache,
        # traffic, cost, failures, source) folds into the plumbing row.
        # The pair tags both eras: bold = the basis the board ranks on.
        proj = _proj_eff(dated, payload, str(row["route"]))
        basis = _basis(row)
        eff_label = rundata.rate_label(row.get("eff_per_mtok")) or "n/a"
        proj_label = rundata.rate_label(proj) if proj is not None else None
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
        iq = _iq_value(str(row["route"]), basis, intel)
        series = rundata.ask_series(dated, str(row["route"]), payload)
        body_rows.append(
            "<tr>"
            f'<th scope="row"><code>{html.escape(str(row["route"]))}</code>'
            f'<span class="route-ask">ask {ask_in} / {ask_out} per M{mark}</span></th>'
            + _viz_cell(
                pair,
                rundata.log_bar_pct(basis, 0.001, 10.0),
                rundata.rate_color_class(basis),
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
        if row.get("marginal_per_mtok") is not None:
            marg_label = rundata.rate_label(row.get("marginal_per_mtok")) or "n/a"
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
            plumb_cells.append((f"marginal $/M <span title=\"{marg_tip}\">&#9432;</span>", marg_label))
        body_rows.append(_plumb_row(plumb_cells))
    caption = (
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
    return (
        '<section class="pricing-block" id="pricing">'
        f"<h2>{html.escape(section_title('pricing'))}</h2>"
        f'<p class="section-note">{html.escape(note)}.</p>'
        '<div class="scroll"><table class="pricing">'
        f"<caption>{caption}</caption>"
        "<thead><tr>"
        '<th scope="col">Route</th>'
        '<th scope="col" class="num">effective $/M</th>'
        '<th scope="col" class="num">IQ per $</th>'
        "</tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody>"
        "</table></div>"
        + spend_block(payload, runs)
        + evidence_block(payload, rundata.load_catalog(ROOT), dated)
        + "</section>"
    )


def evidence_block(payload: dict | None, catalog: dict | None,
                   dated: list | None = None) -> str:
    """The EVIDENCE layer: official-price and reliability, open on demand.

    Sits at the bottom of #pricing; collapsed by default so the decision
    surface (verdict + board) stays uncluttered. Renders only when at
    least one sub-table has data. `dated` snapshots switch the official
    table's forward columns onto the smoothed projection hit rate.
    """
    official = official_table(payload, catalog, dated)
    failures = failures_table(payload)
    if not official and not failures:
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
                f'<td class="num" colspan="3">{gap}</td>'
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
            f'<td class="num">{rundata.rate_label(r["ih_eff"]) or "n/a"}</td>'
            f'<td class="num">{rundata.rate_label(r["off_eff"]) or "n/a"}</td>'
            f'<td class="num">{ratio}</td>'
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
    # Legacy "errors" key until the 2026-09-02 sweep renames it — same
    # fallback _route_failures uses; remove both after the sweep lands.
    failures = payload.get("failures") or payload.get("errors") or {}
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
            f'<td class="num">{m_reqs}</td>'
            f'<td class="num">{m_failed}</td>'
            f'<td class="num">{rate_cell}</td>'
            f'<td class="num">{html.escape(m_codes) or "&#8212;"}</td>'
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


# ── probe results section ──


def _resolved_for(run: dict, route: str, candidate: bool) -> str:
    cells = rundata.candidate_cells(run) if candidate else rundata.board_cells(run)
    resolved = ""
    for cell in cells:
        if cell.get("alias") == route:
            resolved = cell.get("resolved_model") or resolved
    return resolved


def _effective_probe(run: dict, alias: str, score_ids: list[str]) -> tuple[int, int, bool]:
    """(ok, total, probed) for an alias from the run's board cells — or,
    for a seat that joined the board after the sweep probed it, from its
    latest audition cells."""
    if rundata.alias_probed(run, alias):
        ok, total = rundata.scoring_pass_count(run, alias, score_ids)
        return ok, total, True
    if any(c.get("alias") == alias for c in rundata.candidate_cells(run)):
        ok, total = rundata.candidate_pass_count(run, alias, score_ids)
        return ok, total, True
    ok, total = rundata.scoring_pass_count(run, alias, score_ids)
    return ok, total, False


def _candidate_route_row(
    runs: list[dict],
    route_entries: dict,
    route: str,
    score_ids: list[str],
    *,
    candidate: bool,
) -> str:
    """One route row for the candidates tables (incumbent or audition)."""
    latest = runs[-1]
    entry = route_entries.get(route) or {}
    source_candidate = candidate
    if candidate:
        ok, total = rundata.candidate_pass_count(latest, route, score_ids)
        failed = rundata.candidate_failed_ids(latest, route, score_ids)
        probed = any(c.get("alias") == route for c in rundata.candidate_cells(latest))
        cache_raw = rundata.candidate_cache_pct(latest, route)
    else:
        ok, total, probed = _effective_probe(latest, route, score_ids)
        source_candidate = probed and not rundata.alias_probed(latest, route)
        failed = (
            rundata.candidate_failed_ids(latest, route, score_ids)
            if source_candidate
            else rundata.scoring_failed_ids(latest, route, score_ids)
        )
        cache_raw = entry.get("cache_pct")
    pill = "" if candidate else ' <span class="pill in-use">in use</span>'
    resolved = _resolved_for(latest, route, source_candidate)
    if probed:
        probe_val = f"{ok}/{total}"
        if total and ok == total:
            val_cls = "tests-ok"
        elif ok:
            val_cls = "tests-mid"
        else:
            val_cls = "tests-bad"
        miss = ", ".join(rundata.scoring_short(cid) for cid in failed)
        probe_sub = f'<span class="route-ask">missed: {html.escape(miss)}</span>' if miss else ""
        cell_title = (
            ' data-tip="Scoring checks passed in the latest probe"'
            if not miss
            else ' data-tip="Scoring checks passed in the latest probe'
            f" &#8212; failed: {html.escape(miss)}\""
        )
    else:
        probe_val = "&#8212;"
        probe_sub = ""
        val_cls = "tests-none"
        cell_title = ' data-tip="Not probed in the latest run"'
    ask_in = rundata.rate_label(entry.get("ask_in")) or "n/a"
    ask_out = rundata.rate_label(entry.get("ask_out")) or "n/a"
    passed, seen = rundata.route_window_record(runs, route, score_ids, candidate)
    window = f"{passed}/{seen}" if seen else "&#8212;"
    return (
        "<tr>"
        f'<th scope="row"><code>{html.escape(route)}</code>{pill}'
        f'<span class="route-ask">{html.escape(publisher_label(resolved))}</span></th>'
        f'<td class="num" data-label="tests"{cell_title}><span class="{val_cls}">{probe_val}</span>{probe_sub}</td>'
        + _viz_cell(
            rundata.cache_label(cache_raw) or "n/a",
            rundata.cache_bar_pct(cache_raw),
            rundata.cache_color_class(cache_raw),
            data_label="cache hit",
            data_tip="Prompt-cache share — board routes from the 30-day billing window, audition routes from probe evidence.",
        )
        + f'<td class="num" data-label="ask in / out" data-tip="Ask price per M tokens (input / output); audition routes are billed on probe traffic.">{ask_in} / {ask_out}</td>'
        + f'<td class="num" data-label="window" data-tip="All-pass runs / probed runs since the route was first seen.">{window}</td>'
        "</tr>"
    )


def _chip_cls(ok: int, total: int) -> str:
    """Summary-chip tone for a test score: ok / mid / bad / dim (unprobed)."""
    if not total:
        return "dim"
    if ok == total:
        return "ok"
    return "mid" if ok else "bad"


# In-use $/M billed on fewer requests than this is a probe-only sample —
# real traffic has not built up yet, so the chip carries a marker.
PROBE_ONLY_MAX_REQS = 25


def _bar_provenance(verdict: dict) -> tuple[str, bool]:
    """Tooltip clause for the in-use bar; True when the sample is probe-only."""
    reqs = verdict.get("incumbent_reqs") or 0
    if reqs < PROBE_ONLY_MAX_REQS:
        plural = "s" if reqs != 1 else ""
        return (
            f"probe-only sample ({reqs} request{plural}) — no real traffic yet",
            True,
        )
    return f"billed across {reqs} requests in the 30-day window", False


def _price_chip_html(verdict: dict | None) -> str:
    """Best-price chip: in-use $/M vs the cheapest passing challenger.

    Tone — ok: no challenger undercuts the incumbent; mid: challenger
    undercuts (warning, any margin). Empty string without verdict data.
    A `*` after the in-use rate marks a probe-only billing sample.
    """
    if not verdict or verdict.get("incumbent_usd_m") is None:
        return ""
    in_use = rundata.rate_label(verdict["incumbent_usd_m"])
    bar_note, probe_only = _bar_provenance(verdict)
    star = "*" if probe_only else ""
    incumbent = html.escape(str(verdict.get("incumbent") or ""))
    if verdict.get("challenger") is None:
        return (
            f'<span class="chip price ok" title="In-use {incumbent}: {bar_note}. '
            f'No passing route bills cheaper.">in use {in_use}/M{star} · best</span>'
        )
    best = rundata.rate_label(verdict["challenger_usd_m"])
    margin = verdict["margin_pct"]
    tone = "mid"
    cache_pct = verdict.get("challenger_cache_pct")
    cache_src = verdict.get("challenger_cache_source")
    if cache_pct is not None:
        cache_note = f"{cache_pct:.0f}% {'measured probe' if cache_src == 'probe' else 'family'} cache"
    else:
        cache_note = "family cache"
    return (
        f'<span class="chip price {tone}" title="In-use {incumbent}: {bar_note}. '
        f'Challenger {html.escape(verdict["challenger"])}: predicted from asks '
        f'× {cache_note} × the family token mix.">in use {in_use}/M{star} · '
        f"best {best}/M (&#8722;{margin:.0f}%)</span>"
    )


def probe_results_section(
    runs: list[dict], aliases: list[str], registry: list[dict], payload: dict | None
) -> str:
    """The #results section, or '' without candidate cells in the latest run."""
    if not runs:
        return ""
    latest = runs[-1]
    groups = run_groups(latest)
    if not groups:
        return ""
    score_ids = rundata.scoring_ids(registry)
    route_entries = (payload or {}).get("routes") or {}
    try:
        verdict_by_fam = {
            v["family"]: v for v in radar.family_verdicts(latest, route_entries, aliases)
        }
    except Exception as exc:  # noqa: BLE001 — chips are optional; the site must build
        print(f"generate: price chips skipped: {exc}", file=sys.stderr)
        verdict_by_fam = {}
    blocks = []
    for group in groups:
        incumbents = rundata.incumbent_aliases(aliases, group["model"])
        rows_html = []
        for alias in incumbents:
            rows_html.append(
                _candidate_route_row(runs, route_entries, alias, score_ids, candidate=False)
            )
        ranked = []
        incumbent_set = set(incumbents)
        for route in group["routes"]:
            if route in incumbent_set:
                continue  # the board row already renders this route
            ok, _ = rundata.candidate_pass_count(latest, route, score_ids)
            cache = rundata.candidate_cache_pct(latest, route)
            entry = route_entries.get(route) or {}
            ask_in, ask_out = entry.get("ask_in"), entry.get("ask_out")
            blended = (
                (ask_in + ask_out) / 2
                if ask_in is not None and ask_out is not None
                else None
            )
            ranked.append((route, ok, cache, blended))
        ranked.sort(
            key=lambda r: (
                -r[1],
                -(r[2] if r[2] is not None else -1.0),
                r[3] if r[3] is not None else float("inf"),
                r[0],
            )
        )
        for route, *_ in ranked:
            rows_html.append(
                _candidate_route_row(runs, route_entries, route, score_ids, candidate=True)
            )
        if not rows_html:
            continue
        chips = []
        if incumbents:
            best = max(
                incumbents,
                key=lambda a: _effective_probe(latest, a, score_ids)[0],
            )
            ok, total, probed = _effective_probe(latest, best, score_ids)
            chips.append(
                f'<span class="chip {_chip_cls(ok, total) if probed else "dim"}">'
                f"{html.escape(best)} · {ok}/{total}</span>"
            )
        else:
            chips.append('<span class="chip dim">no incumbent</span>')
        if ranked:
            route, ok, cache, _ = ranked[0]
            total = len(score_ids)
            cache_bit = f" · {rundata.cache_label(cache)}" if cache is not None else ""
            chips.append(
                f'<span class="chip {_chip_cls(ok, total)}">'
                f"{html.escape(route)} · {ok}/{total}{cache_bit}</span>"
            )
        fam_alias = incumbents[0] if incumbents else (ranked[0][0] if ranked else None)
        if fam_alias:
            price_chip = _price_chip_html(verdict_by_fam.get(market.family(fam_alias)))
            if price_chip:
                chips.append(price_chip)
        blocks.append(
            '<details class="model-group">'
            f'<summary><span class="model-name">{html.escape(group["model"])}</span>'
            f"{''.join(chips)}</summary>"
            '<div class="scroll"><table class="pricing candidates">'
            "<thead><tr>"
            '<th scope="col" title="Provider route; the in-use pill marks the route currently on the board.">Route</th>'
            '<th scope="col" class="num" title="Scoring checks passed in the latest probe; hover a value for the failed ones.">tests</th>'
            '<th scope="col" class="num" title="Prompt-cache share — board routes from the 30-day billing window, audition routes from probe evidence.">cache hit</th>'
            '<th scope="col" class="num" title="Ask price per M tokens (input / output); audition routes are billed on probe traffic.">ask in / out</th>'
            '<th scope="col" class="num" title="All-pass runs / probed runs since the route was first seen.">window</th>'
            "</tr></thead>"
            f"<tbody>{''.join(rows_html)}</tbody></table></div>"
            "</details>"
        )
    if not blocks:
        return ""
    note = (
        "Board routes in current use (&#8220;in use&#8221; pill) plus audition routes "
        "from the market shortlist — live catalog asks ranked by predicted $/M, "
        "cheaper-than-in-use only — grouped by model and probed after each board sweep. "
        "Audition routes rank by checks passed, then cache hit, then blended ask. "
        "Cache share: board routes from the 30-day billing window, audition routes "
        "from the probe. Window = runs all-pass / runs probed since first seen. "
        "Audition asks are billed on probe traffic."
    )
    return (
        '<section class="probe-results" id="results">'
        f"<h2>{html.escape(section_title('results'))}</h2>"
        f'<p class="section-note">{note}</p>'
        + "".join(blocks)
        + "</section>"
    )


def index_html(runs: list[dict], aliases: list[str], registry: list[dict]) -> str:
    if not runs:
        return shell("InferHub Watch", tmpl.render("empty.html"))

    latest = runs[-1]
    window = runs[-14:]
    score_ids = rundata.scoring_ids(registry)
    n_score = len(score_ids)
    order = rundata.aliases_safe_first(aliases, latest, score_ids)
    rule = rundata.scoring_rule(score_ids)

    col_labels = [
        f"{rundata.run_stamp(run)} {rundata.origin_label(run)}" for run in window
    ]
    grid_rows = []
    for alias in order:
        cells = []
        for run, col_label in zip(window, col_labels):
            if not rundata.alias_probed(run, alias):
                cells.append(
                    '<td class="absent" data-tip="'
                    f'{html.escape(col_label + " · not probed")}"'
                    ' tabindex="0"></td>'
                )
                continue
            ok, total = rundata.scoring_pass_count(run, alias, score_ids)
            cls = "ok" if ok == total else ("mid" if ok else "bad")
            failed = rundata.scoring_failed_ids(run, alias, score_ids)
            title_parts = [col_label, f"{ok}/{total}"]
            if failed:
                miss = ", ".join(rundata.scoring_short(cid) for cid in failed)
                title_parts.append(f"missed: {miss}")
            else:
                title_parts.append("all pass")
            bar_cost = rundata.alias_run_cost(run, alias)
            if bar_cost:
                title_parts.append(f"cost {bar_cost}")
            title = " · ".join(title_parts)
            cells.append(
                f'<td class="{cls}" data-tip="{html.escape(title)}"'
                ' tabindex="0"></td>'
            )
        resolved = rundata.resolved_for_alias_in_window(window, alias, registry)
        grid_rows.append(f"<tr>{alias_heading(alias, resolved)}{''.join(cells)}</tr>")

    explainers = []
    for spec in registry:
        brief = mdhtml.check_brief_html(ROOT, spec)
        explainers.append(
            f'<details id="check-{html.escape(spec["id"])}">'
            f"<summary>{html.escape(spec['title'])}</summary>"
            f'<div class="check-brief">{brief}</div></details>'
        )

    started_raw = (latest.get("started_at") or "")[:19]
    started = html.escape(started_raw.replace("T", " ") + " UTC")
    run_cost = rundata.run_total_cost(latest)
    cost_bit = f' · run cost <span class="run-cost">{run_cost}</span>' if run_cost else ""
    header_meta = (
        f'<p class="probe-meta">Last probe: '
        f'<time datetime="{html.escape(started_raw)}">{started}</time>'
        f"{cost_bit}</p>"
    )
    payload = rundata.load_pricing(ROOT)
    body = tmpl.render(
        "board.html",
        earlier_title=section_title("earlier"),
        method_title=section_title("method"),
        n_score=str(n_score),
        score_label="check" if n_score == 1 else "checks",
        rule=rule,
        grid_rows="".join(grid_rows),
        verdict_section=verdict_section(payload),
        pricing_section=pricing_section(payload, runs),
        probe_results_section=probe_results_section(
            runs, aliases, registry, payload
        ),
        explainers="".join(explainers),
        github=GITHUB,
        clone=CLONE,
    )
    return shell(
        "InferHub Watch",
        body,
        page_class="board",
        with_footer=False,
        page_nav=board_nav(),
        header_meta=header_meta,
    )


def check_page(spec: dict) -> str:
    md = (ROOT / "checks" / spec["id"] / "page.md").read_text()
    body = tmpl.render("check.html", article=mdhtml.md_to_html(md))
    return shell(
        spec["title"], body, crumb=spec["title"], nested=True, page_class="brief"
    )


def main() -> int:
    dist = ROOT / "site" / "dist"
    if dist.exists():
        shutil.rmtree(dist)
    dist.mkdir(parents=True)
    shutil.copy(ROOT / "site" / "style.css", dist / "style.css")
    aliases = load_aliases()
    registry = load_registry()
    runs = load_runs()
    (dist / "index.html").write_text(index_html(runs, aliases, registry))
    checks_dir = dist / "checks"
    checks_dir.mkdir()
    for spec in registry:
        (checks_dir / f"{spec['id']}.html").write_text(check_page(spec))
    print(dist)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
