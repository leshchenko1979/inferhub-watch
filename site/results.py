"""Probe results: the merged family table (incumbents + audition routes).

Split out of generate.py (P2b section-module carve). One row per route
per family band — incumbent first, then audition routes ranked by checks
passed, cache hit, blended ask — with the price chip per family.
"""
from __future__ import annotations

import html
import sys

import rundata
from probe import market, radar
from probe.publishers import publisher_label
from probe.registry import repo_root

from chrome import _viz_cell, base_href, section_title
from rundata import run_groups

ROOT = repo_root()

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


def _in_use_pill(reqs_24h: int) -> str:
    """'in use' pill, derived from 24h usage — same rule as the chart.

    Owner 2026-09-07: the old pill was unconditional on every incumbent
    route. Now in-use requires >=100 billed requests in the newest 24h
    (board.usage_color's IN_USE_MIN_REQS, fed by the perf block's per-model
    24h request count — the same number the scatter colors on); routes
    below the bar show a muted 'light use' pill instead."""
    if reqs_24h >= 100:
        return (' <span class="pill in-use" title="In use: 100+ billed '
                'requests in the newest 24h.">in use</span>')
    return (
        f' <span class="pill in-use--light" title="Billed traffic below the '
        f'in-use bar (100+ reqs/24h): {reqs_24h} in the newest 24h.">light use</span>'
    )

def _candidate_route_row(
    runs: list[dict],
    route_entries: dict,
    route: str,
    score_ids: list[str],
    *,
    candidate: bool,
    perf_models: dict | None = None,
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
    pill = _in_use_pill(
        ((perf_models or {}).get(route) or {}).get("reqs") or 0
    )
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
    ask_in = rundata.rate_label(entry.get("ask_in"), fixed=4) or "n/a"
    ask_out = rundata.rate_label(entry.get("ask_out"), fixed=4) or "n/a"
    passed, seen = rundata.route_window_record(runs, route, score_ids, candidate)
    window = f"{passed}/{seen}" if seen else "&#8212;"
    ttft_ms, tps = rundata.stream_perf_of(latest, route)
    ttft_label = f"{ttft_ms / 1000:.1f}s" if ttft_ms is not None else "&#8212;"
    tps_label = f"{tps:.0f}" if tps is not None else "&#8212;"
    perf_tip = "First-token latency and tokens/sec from the latest core probe (one streamed request; dash = pre-perf run)."
    # Drill-down: each route links to the per-check pages (Level-3 IA).
    base = base_href()
    if base:
        core_href = f"{base}/checks/core.html"
        cache_href = f"{base}/checks/cache.html"
    else:
        core_href = "../checks/core.html"
        cache_href = "../checks/cache.html"
    drill = (
        f'<span class="route-drill">'
        f'<a href="{core_href}" title="Open the core check page — request JSON and pass/fail rule">core</a>'
        f' · <a href="{cache_href}" title="Open the cache check page — request JSON and pass/fail rule">cache</a>'
        f"</span>"
    )
    return (
        "<tr>"
        f'<th scope="row"><code>{html.escape(route)}</code>{pill}{drill}'
        f'<span class="route-ask">{html.escape(publisher_label(resolved))}</span></th>'
        f'<td class="num" data-label="tests"{cell_title}><span class="{val_cls}">{probe_val}</span>{probe_sub}</td>'
        + _viz_cell(
            rundata.cache_label(cache_raw) or "n/a",
            rundata.cache_bar_pct(cache_raw),
            rundata.cache_color_class(cache_raw),
            data_label="cache hit",
            data_tip="Prompt-cache share — board routes from the 30-day billing window, audition routes from probe evidence.",
        )
        + f'<td class="num" data-label="ttft" data-tip="{perf_tip}">{ttft_label}</td>'
        + f'<td class="num" data-label="tps" data-tip="{perf_tip}">{tps_label}</td>'
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
    in_use = rundata.rate_label(verdict["incumbent_usd_m"], fixed=4)
    bar_note, probe_only = _bar_provenance(verdict)
    star = "*" if probe_only else ""
    incumbent = html.escape(str(verdict.get("incumbent") or ""))
    if verdict.get("challenger") is None:
        return (
            f'<span class="chip price ok" title="In-use {incumbent}: {bar_note}. '
            f'No passing route bills cheaper.">in use {in_use}/M{star} · best</span>'
        )
    best = rundata.rate_label(verdict["challenger_usd_m"], fixed=4)
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
    perf_models = ((payload or {}).get("perf") or {}).get("models") or {}
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
        rows_html = [
            _candidate_route_row(runs, route_entries, alias, score_ids,
                                 candidate=False, perf_models=perf_models)
            for alias in incumbents
        ]
        rows_html += [
            _candidate_route_row(runs, route_entries, route, score_ids,
                                 candidate=True, perf_models=perf_models)
            for route, *_ in ranked
        ]
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
            (f'<tr class="fam-head"><th colspan="7" id="fam-{html.escape(group["model"])}">'
             f'{html.escape(group["model"])}{"".join(chips)}</th></tr>')
            + "".join(rows_html)
        )
    if not blocks:
        return ""
    note = (
        "Board routes in current use (&#8220;in use&#8221; pill) plus audition routes "
        "from the market shortlist — live catalog asks ranked by predicted $/M, "
        "cheaper-than-in-use only — probed after each board sweep. One merged table: "
        "family bands in reading order, incumbent first, then audition routes by "
        "checks passed, cache hit, blended ask. "
        "Cache share: board routes from the 30-day billing window, audition routes "
        "from the probe. Window = runs all-pass / runs probed since first seen. "
        "Audition asks are billed on probe traffic."
    )
    from tmpl import render

    return render(
        "results.html",
        title=html.escape(section_title("results")),
        note=note,
        blocks="".join(blocks),
    )

