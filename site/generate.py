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

import cellcopy  # noqa: E402
import mdhtml  # noqa: E402
import rundata  # noqa: E402
import tmpl  # noqa: E402
from probe.publishers import publisher_label  # noqa: E402
from probe.registry import load_aliases, load_registry  # noqa: E402

GITHUB = "https://github.com/leshchenko1979/inferhub-watch"
CLONE = (
    f'Clone <a href="{GITHUB}">leshchenko1979/inferhub-watch</a>, '
    "set <code>INFERHUB_API_KEY</code>, run <code>python3 -m probe.run</code>."
)
FONTS = (
    "https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500"
    "&family=Newsreader:ital,opsz,wght@0,8..72,400;0,8..72,600;1,8..72,400&display=swap"
)
SECTIONS = (
    ("probe", "Latest results"),
    ("pricing", "Cost per M tokens"),
    ("earlier", "Past runs"),
    ("method", "How we test"),
)


def base_href() -> str:
    raw = os.environ.get("PAGES_BASE", "/inferhub-watch").rstrip("/")
    return raw or ""


def load_runs() -> list[dict]:
    return rundata.load_runs(ROOT)


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
        crumb=crumb_html,
        body=body,
        footer=f"<footer><p>{CLONE}</p></footer>" if with_footer else "",
        script=script,
    )


def _viz_cell(label: str, bar_pct: float, color_cls: str) -> str:
    """Value on top, a 4px bar underneath (Gatus-style)."""
    bar = (
        f'<i class="{color_cls}" style="width:{bar_pct:.0f}%"></i>'
        if bar_pct > 0
        else ""
    )
    return (
        f'<td class="num viz"><span class="viz-val">{label}</span>'
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
    """MTD + today + probe-share stats with the 30-day sparkline, or ''."""
    days = rundata.spend_days(payload)
    today = _snapshot_day(payload)
    if not days or not today:
        return ""
    mtd = rundata.spend_between(days, today[:7] + "-01", today)
    today_cost = rundata.spend_between(days, today, today)
    first_day = rundata.day_key(runs[0]) if runs else ""
    since = rundata.month_day_label(first_day)
    probe_cap = "probe runs" + (f" · since {since}" if since else "")
    stats = (
        (rundata.cost_label(f"{mtd:.6f}") or "$0.00", "month to date"),
        (rundata.cost_label(f"{today_cost:.6f}") or "$0.00", "today so far"),
        (rundata.cost_label(f"{rundata.probe_spend(runs):.6f}") or "$0.00", probe_cap),
    )
    bits = "".join(
        f'<div class="spend-stat"><span class="spend-val">{val}</span>'
        f'<span class="spend-cap">{html.escape(cap)}</span></div>'
        for val, cap in stats
    )
    return (
        f'<div class="spend-block"><div class="spend-stats">{bits}</div>'
        f"{spend_sparkline(payload)}</div>"
    )


def _delta_span(delta: float) -> str:
    if abs(delta) < 1e-9:
        return '<span class="delta-flat" title="ask unchanged">=</span>'
    mag = rundata.rate_label(abs(delta)) or f"${abs(delta):.3f}"
    if delta < 0:
        return f'<span class="delta-down" title="ask fell">&#8595;{mag}</span>'
    return f'<span class="delta-up" title="ask rose">&#8593;{mag}</span>'


def ask_delta_cell(payload: dict | None, prior: dict | None, route: str) -> str:
    """One Δ ask cell: in/out movement vs the prior snapshot, '—' without one."""
    deltas = rundata.ask_deltas(payload, prior, route)
    if deltas is None:
        return '<td class="num ask-delta" data-label="&#916; ask in / out">' \
            '<span class="delta-flat" title="no earlier snapshot for this route">&#8212;</span></td>'
    return (
        '<td class="num ask-delta" data-label="&#916; ask in / out">'
        + _delta_span(deltas["in"])
        + '<span class="delta-sep"> / </span>'
        + _delta_span(deltas["out"])
        + "</td>"
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
    req_bounds = rundata.peer_bounds(int(r.get("reqs") or 0) for r in rows)
    cost_bounds = rundata.peer_bounds(float(r.get("cost_usdc") or 0) for r in rows)
    prior = rundata.prior_pricing(rundata.load_dated_pricing(ROOT), payload)
    body_rows = []
    for row in rows:
        logged = row.get("source") == "usage-logs"
        mark = "" if logged else '<span class="ask-mark" title="no traffic in window; catalog list price">*</span>'
        ask_in = rundata.rate_label(row.get("ask_in")) or "n/a"
        ask_out = rundata.rate_label(row.get("ask_out")) or "n/a"
        eff_raw = row.get("eff_per_mtok")
        reqs = int(row.get("reqs") or 0)
        toks = rundata.token_label(
            int(row.get("tok_in") or 0) + int(row.get("tok_out") or 0)
        )
        body_rows.append(
            "<tr>"
            f'<th scope="row"><code>{html.escape(str(row["route"]))}</code>'
            f'<span class="route-ask">ask {ask_in} / {ask_out} per M{mark}</span></th>'
            + ask_delta_cell(payload, prior, str(row["route"]))
            + _viz_cell(
                rundata.rate_label(eff_raw) or "n/a",
                rundata.log_bar_pct(eff_raw, 0.001, 10.0),
                rundata.rate_color_class(eff_raw),
            )
            + _viz_cell(
                rundata.cache_label(row.get("cache_pct")) or "n/a",
                rundata.cache_bar_pct(row.get("cache_pct")),
                rundata.cache_color_class(row.get("cache_pct")),
            )
            + _viz_cell(
                f"{reqs} req · {toks} tok",
                rundata.log_bar_pct(reqs, *req_bounds),
                "neutral",
            )
            + _viz_cell(
                rundata.cost_label(row.get("cost_usdc")) or "n/a",
                rundata.log_bar_pct(float(row.get("cost_usdc") or 0), *cost_bounds),
                "gold",
            )
            + "</tr>"
        )
    caption = (
        "&#8220;ask&#8221; under each route is the per-M rate billed on fresh "
        "(uncached) input / output; &#8220;effective&#8221; is billed cost over all "
        "tokens, cache discounts included. Effective bars are log-scaled over "
        "$0.001&#8211;$10 per M and colored green &#8804; $0.02, amber &#8804; $0.20, red above; "
        "cache bars are linear, green &#8805; 70%. Traffic and cost bars are relative "
        "to the busiest route in the window. "
        "* = no traffic in the window, rates fall back to catalog list price. "
        "&#916; ask compares the billed rates with the previous daily snapshot: "
        "&#8595; green = cheaper, &#8593; red = pricier, &#8212; = no earlier snapshot "
        "to compare yet. Sparkline bars are log-scaled $0.001&#8211;$10 per day. "
        "Rates for this board&#8217;s routes only; other traffic is not listed."
    )
    return (
        '<section class="pricing-block" id="pricing">'
        f"<h2>{html.escape(section_title('pricing'))}</h2>"
        f'<p class="section-note">{html.escape(note)}.</p>'
        + spend_block(payload, runs) +
        '<div class="scroll"><table class="pricing">'
        f"<caption>{caption}</caption>"
        "<thead><tr>"
        '<th scope="col">Route</th>'
        '<th scope="col" class="num">&#916; ask in / out</th>'
        '<th scope="col" class="num">effective $/M</th>'
        '<th scope="col" class="num">cache hit</th>'
        f'<th scope="col" class="num">{html.escape(span)} traffic</th>'
        f'<th scope="col" class="num">{html.escape(span)} cost</th>'
        "</tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody>"
        "</table></div></section>"
    )


def index_html(runs: list[dict], aliases: list[str], registry: list[dict]) -> str:
    if not runs:
        return shell("InferHub Watch", tmpl.render("empty.html"))

    latest = runs[-1]
    window = runs[-14:]
    score_ids = rundata.scoring_ids(registry)
    n_score = len(score_ids)
    specs = rundata.display_specs(registry)
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
        resolved = rundata.resolved_for_alias(window[-1], alias, registry)
        grid_rows.append(f"<tr>{alias_heading(alias, resolved)}{''.join(cells)}</tr>")

    cmap = rundata.cell_map(latest)
    check_heads = []
    for spec in specs:
        scoring = bool(spec.get("scores_rank"))
        role = "scores" if scoring else "info · not ranked"
        kind = "col-score" if scoring else "col-info"
        check_heads.append(
            f'<th class="check-col {kind}" scope="col">'
            f'<a href="#check-{html.escape(spec["id"])}">'
            f"{html.escape(spec['title'])}</a>"
            f'<span class="th-role">{html.escape(role)}</span></th>'
        )
    matrix_rows = []
    safe = []
    for alias in order:
        resolved = ""
        check_tds = []
        for spec in specs:
            cell = cmap.get((alias, spec["id"])) or {}
            resolved = cell.get("resolved_model") or resolved
            status = cell.get("status") or "missing"
            summary = cellcopy.note({**cell, "check_id": spec["id"]})
            scoring = bool(spec.get("scores_rank"))
            data_label = html.escape(
                f"{rundata.scoring_short(spec['id'])} · "
                f"{'scores' if scoring else 'info'}"
            )
            if scoring:
                inner = (
                    f'<span class="pill">{html.escape(status)}</span>'
                    f'<p class="cell-note">{html.escape(summary)}</p>'
                )
            else:
                inner = f'<p class="cell-note info-note">{html.escape(summary)}</p>'
            check_tds.append(
                f'<td class="st-{html.escape(status)}" data-label="{data_label}">'
                f"{inner}</td>"
            )
        ok, total = rundata.scoring_pass_count(latest, alias, score_ids)
        row_attr = ' class="row-safe"' if total and ok == total else ""
        matrix_rows.append(
            f"<tr{row_attr}>{alias_heading(alias, resolved)}{''.join(check_tds)}</tr>"
        )
        if total and ok == total:
            safe.append(alias)

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
    dispatch = (
        f'<p class="dispatch-meta">Last probe: '
        f'<time datetime="{html.escape(started_raw)}">{started}</time>'
        f"{cost_bit}</p>"
    )
    if safe:
        rec = ", ".join(f"<code>{html.escape(a)}</code>" for a in safe)
        line = f"{rec}."
    else:
        line = "No alias is safe to use this run."
    recommend = (
        f'<div class="verdict"><h1>Safe to use</h1>'
        f'<p class="verdict-line">{line}</p></div>'
    )
    body = tmpl.render(
        "board.html",
        probe_title=section_title("probe"),
        earlier_title=section_title("earlier"),
        method_title=section_title("method"),
        recommend=recommend,
        dispatch=dispatch,
        check_heads="".join(check_heads),
        matrix_rows="".join(matrix_rows),
        n_score=str(n_score),
        score_label="check" if n_score == 1 else "checks",
        rule=rule,
        grid_rows="".join(grid_rows),
        pricing_section=pricing_section(rundata.load_pricing(ROOT), runs),
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
