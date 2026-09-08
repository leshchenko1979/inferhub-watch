"""Spend dashboard: 30-day sparkline, MTD/today/probe stats, ask deltas.

Split out of generate.py (P2b section-module carve). Pure presentation over
rundata accessors; renders the collapsed spend fold and the ask-movement
bits the board's plumbing rows consume.
"""
from __future__ import annotations

import html
from datetime import date, timedelta

import rundata

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
            # A day INSIDE the fetched series that shows zero = genuinely no
            # billed traffic. A day entirely missing from `days` when the
            # series itself is short = the window was truncated at fetch
            # time — say so, do not dress it up as "no traffic" (owner
            # 2026-09-07: "why only two days on the graph?").
            if entry is None and len(days) < SPARK_DAYS:
                suffix = " · outside the fetched window"
                cls = "spark-missing"
            else:
                suffix = " · no billed traffic"
                cls = "spark-zero"
            bars.append(
                f'<rect class="{cls}" x="{x}" y="{SPARK_BAR_H - 2}" '
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
    SPEND_TIPS = {
        "mtd": "Total InferHub bill for the calendar month so far, across all routes and traffic.",
        "today": "Bill so far today (UTC day), all routes.",
        "probe": "Spend attributable to probe runs only — the cost of running this watch, separate from real traffic.",
    }
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
        (mtd_label, "month to date", "mtd"),
        (rundata.cost_label(f"{today_cost:.6f}") or "$0.00", "today so far", "today"),
        (rundata.cost_label(f"{rundata.probe_spend(runs):.6f}") or "$0.00", probe_cap, "probe"),
    )
    bits = "".join(
        f'<div class="spend-stat" data-tip="{SPEND_TIPS[cap_key]}">'
        f'<span class="spend-val">{val}</span>'
        f'<span class="spend-cap">{html.escape(cap)}</span></div>'
        for val, cap, cap_key in stats
    )
    return (
        '<details class="evidence-item spend-item" id="spend">'
        f"<summary>Spend &#8212; {mtd_label} month to date</summary>"
        f'<div><div class="spend-block"><div class="spend-stats">{bits}</div>'
        f"{spend_sparkline(payload)}</div></div></details>"
    )


def _delta_span(delta: float) -> str:
    if abs(delta) < 1e-9:
        return '<span class="delta-flat" data-tip="ask unchanged">=</span>'
    mag = rundata.rate_label(abs(delta), fixed=4) or f"${abs(delta):.4f}"
    if delta < 0:
        return f'<span class="delta-down" data-tip="ask fell">&#8595;{mag}</span>'
    return f'<span class="delta-up" data-tip="ask rose">&#8593;{mag}</span>'


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
            'data-tip="no earlier snapshot for this route">&#8212;</span>'
        )
    return (
        _delta_span(deltas["in"])
        + '<span class="delta-sep"> / </span>'
        + _delta_span(deltas["out"])
    )


def _ask_spark(points: list[tuple[str, float, float]]) -> str:
    """Little inline-SVG line graph of a route's ask history, or ''.

    Two lines on one shared scale (honest: in and out stay proportional);
    ask_in keeps the teal stroke, ask_out amber (cheaper/pricier pair —
    bug 2026-09-08: --mid had become amber, so both lines were one hue).
    The colors are the only distinction (owner 2026-09-08: no point marks).
    Needs >= 2 points; days without a logged ask are absent from `points`.
    """
    if len(points) < 2:
        return ""
    w, h, pad = 120.0, 26.0, 2.5
    n = len(points)
    hi = max(max(p[1] for p in points), max(p[2] for p in points)) or 1.0

    def polyline(vals: list[float], line_idx: int) -> str:
        coords = []
        for i, v in enumerate(vals):
            x = pad + i * (w - 2 * pad) / (n - 1)
            y = h - pad - (v / hi) * (h - 2 * pad)
            coords.append((x, y))
        path = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
        cls = "s-line" if line_idx == 0 else "s-line s-line-out"
        return f'<polyline class="{cls}" points="{path}"/>'

    in_path = polyline([p[1] for p in points], 0)
    out_path = polyline([p[2] for p in points], 1)
    first, last = points[0][0], points[-1][0]
    # Tip carries first + last only (QA: the full per-day data wall hit
    # 300+ chars); the spark itself is the trend.
    a0, o0 = points[0][1], points[0][2]
    a1, o1 = points[-1][1], points[-1][2]
    tip = (
        f"{first}: {a0:g}/{o0:g} → {last}: {a1:g}/{o1:g}"
        f" ({n} days)"
    )
    return (
        f'<svg class="ask-spark" width="{w:g}" height="{h:g}" viewBox="0 0 {w:g} {h:g}" '
        f'role="img" aria-label="ask history {first} to {last}" '
        f'data-tip="Ask $/M, {html.escape(tip)}">'
        f'<title>ask in/out, {html.escape(first)} &#8594; {html.escape(last)}</title>'
        f"{in_path}{out_path}</svg>"
    )
