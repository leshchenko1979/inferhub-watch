from __future__ import annotations

import json
import math
import tomllib
from pathlib import Path

from probe import market, pricing, registry
from probe.registry import repo_root

# Plausible ceiling for a single-request streamed tps spot sample. Above
# this, the probe cell's timing describes something other than generation
# (see stream_perf_of) — render dash instead of the artifact.
TPS_CAP = 500.0


def load_runs(root: Path) -> list[dict]:
    files = sorted((root / "data" / "runs").glob("*.json"))
    return [json.loads(path.read_text()) for path in files]


def day_key(run: dict) -> str:
    return (run.get("started_at") or "")[:10]


def board_cells(run: dict) -> list[dict]:
    """Cells of the board proper; candidate-sweep cells carry a flag and stay out."""
    return [c for c in run.get("cells") or [] if not c.get("candidate")]


def candidate_cells(run: dict) -> list[dict]:
    """Cells of candidate-sweep routes, tagged by the probe layer."""
    return [c for c in run.get("cells") or [] if c.get("candidate")]


def cell_map(run: dict) -> dict[tuple[str, str], dict]:
    return {(c["alias"], c["check_id"]): c for c in board_cells(run)}


def candidate_cell_map(run: dict) -> dict[tuple[str, str], dict]:
    return {(c["alias"], c["check_id"]): c for c in candidate_cells(run)}


def alias_probed(run: dict, alias: str) -> bool:
    """True when the run holds at least one board cell for the alias."""
    return any(cell.get("alias") == alias for cell in board_cells(run))


def scoring_ids(registry: list[dict]) -> list[str]:
    return [spec["id"] for spec in registry if spec.get("scores_rank")]


def display_specs(registry: list[dict]) -> list[dict]:
    scoring = [spec for spec in registry if spec.get("scores_rank")]
    rest = [spec for spec in registry if not spec.get("scores_rank")]
    return scoring + rest


def scoring_short(check_id: str) -> str:
    return check_id.replace("_", " ")


def scoring_rule(check_ids: list[str]) -> str:
    n = len(check_ids)
    joined = " + ".join(scoring_short(check_id) for check_id in check_ids)
    return f"{n}/{n}: {joined}" if joined else f"{n}/{n}"


def _pass_count(cmap: dict[tuple[str, str], dict], key: str, check_ids: list[str]) -> tuple[int, int]:
    ok = sum(
        1
        for check_id in check_ids
        if (cell := cmap.get((key, check_id))) and cell.get("status") == "pass"
    )
    return ok, len(check_ids)


def _failed_ids(cmap: dict[tuple[str, str], dict], key: str, check_ids: list[str]) -> list[str]:
    failed: list[str] = []
    for check_id in check_ids:
        cell = cmap.get((key, check_id))
        if not cell or cell.get("status") != "pass":
            failed.append(check_id)
    return failed


def scoring_pass_count(run: dict, alias: str, check_ids: list[str]) -> tuple[int, int]:
    return _pass_count(cell_map(run), alias, check_ids)


def scoring_failed_ids(run: dict, alias: str, check_ids: list[str]) -> list[str]:
    return _failed_ids(cell_map(run), alias, check_ids)


def candidate_pass_count(run: dict, route: str, check_ids: list[str]) -> tuple[int, int]:
    return _pass_count(candidate_cell_map(run), route, check_ids)


def candidate_failed_ids(run: dict, route: str, check_ids: list[str]) -> list[str]:
    return _failed_ids(candidate_cell_map(run), route, check_ids)


def candidate_cache_pct(run: dict, route: str) -> float | None:
    """Cache-hit share from the route's cache probe cell; None without one."""
    cell = candidate_cell_map(run).get((route, "cache")) or {}
    ev = cell.get("evidence") or {}
    usage = ev.get("usage") or {}
    try:
        cached = float(ev.get("cached_tokens"))
        prompt = float(usage.get("prompt_tokens"))
    except (TypeError, ValueError):
        return None
    if prompt <= 0:
        return None
    return min(100.0, cached / prompt * 100.0)


def stream_perf_of(run: dict, route: str) -> tuple[float | None, float | None]:
    """(ttft_ms, tps) from the route's core probe cell; (None, None) without.

    Older run JSON carries neither field (dash renders); any present value
    passes through. TPS is probe-observed tokens/sec on the core payload's
    single request — a spot sample, not a sustained rate.
    """
    all_cells = board_cells(run) + candidate_cells(run)
    cell = next(
        (c for c in all_cells if c.get("alias") == route and c.get("check_id") == "core"),
        {},
    )
    ttft = cell.get("ttft_ms")
    tps = cell.get("tps")
    # Same guard as the main-traffic perf_stats law (probe/pricing.py): a
    # generation window under ~2s with non-trivial output means the timing
    # fields don't describe streaming generation — division there yields
    # absurd tps (128,779 seen live on zai). Probe cells carry no generation
    # window, so cap the spot sample: a plausible sustained rate for a
    # single streamed request stays under TPS_CAP; anything above is an
    # artifact, rendered as dash rather than poisoning the table.
    if isinstance(tps, (int, float)) and (tps <= 0 or tps >= TPS_CAP):
        tps = None
    return (
        ttft if isinstance(ttft, (int, float)) else None,
        tps if isinstance(tps, (int, float)) else None,
    )


def ttft_tps_labels(ttft_ms: float | int | None, tps: float | int | None,
                    dash: str = "&#8212;") -> tuple[str, str]:
    """(ttft label, tps label) with the single shared precision - used by
    the probes table's per-route cells and the board's plumbing row alike,
    so the same metric never shows two different roundings (review R2).
    Precision: ttft seconds to 2dp, tps to 1dp; dash without a sample."""
    ttft_label = f"{ttft_ms / 1000:.2f}s" if isinstance(ttft_ms, (int, float)) else dash
    tps_label = f"{tps:.1f}" if isinstance(tps, (int, float)) else dash
    return ttft_label, tps_label


def route_window_record(
    runs: list[dict], route: str, check_ids: list[str], candidate: bool
) -> tuple[int, int]:
    """(all-pass runs, probed runs) for a route across the recorded window."""
    probed = passed = 0
    for run in runs:
        cells = candidate_cells(run) if candidate else board_cells(run)
        cmap = {(c["alias"], c["check_id"]): c for c in cells}
        if not any(alias == route for alias, _ in cmap):
            continue
        probed += 1
        ok, total = _pass_count(cmap, route, check_ids)
        if total and ok == total:
            passed += 1
    return passed, probed


def incumbent_aliases(aliases: list[str], model: str) -> list[str]:
    """Board aliases serving the family — a market.family() match, so
    dated board tails (ali/deepseek-v4-flash-0731) still join their
    family's group ("deepseek-v4-flash")."""
    return [a for a in aliases if market.family(a) == model]


def aliases_safe_first(
    aliases: list[str], run: dict, check_ids: list[str]
) -> list[str]:
    index = {alias: i for i, alias in enumerate(aliases)}

    def key(alias: str) -> tuple[int, int]:
        ok, total = scoring_pass_count(run, alias, check_ids)
        safe = 0 if total and ok == total else 1
        return (safe, index[alias])

    return sorted(aliases, key=key)


def run_stamp(run: dict) -> str:
    raw = run.get("started_at") or ""
    day = raw[5:10] if len(raw) >= 10 else day_key(run)
    clock = raw[11:16] if len(raw) >= 16 else ""
    return f"{day} {clock}".strip()


def origin_label(run: dict) -> str:
    raw = (run.get("origin") or "").strip()
    if raw == "github-actions":
        return "Actions · CI"
    if raw.endswith("-seed") or "seed" in raw:
        return "seed · fixture"
    return raw or "run"


def resolved_for_alias(run: dict, alias: str, registry: list[dict]) -> str:
    cmap = cell_map(run)
    resolved = ""
    for spec in registry:
        cell = cmap.get((alias, spec["id"])) or {}
        resolved = cell.get("resolved_model") or resolved
    return resolved


def resolved_for_alias_in_window(
    window: list[dict], alias: str, registry: list[dict]
) -> str:
    """Newest resolved_model for the alias across the window, newest run first.

    A fully failed run can come home with no resolved id at all — the
    publisher label is identity, so fall back to the last run that carried it.
    """
    for run in reversed(window):
        resolved = resolved_for_alias(run, alias, registry)
        if resolved:
            return resolved
    return ""


def cost_label(raw: object) -> str:
    """'0.000712' -> '$0.0007' (six decimals when too small). '' when absent."""
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    text = f"${value:.4f}"
    return text if text != "$0.0000" else f"${value:.6f}"


def _sum_costs(cells: list[dict], alias: str | None = None) -> str:
    total = 0.0
    seen = False
    for cell in cells:
        if alias is not None and cell.get("alias") != alias:
            continue
        raw = cell.get("cost_usdc")
        if not raw:
            continue
        try:
            total += float(raw)
            seen = True
        except ValueError:
            continue
    return cost_label(f"{total:.6f}") if seen else ""


def alias_run_cost(run: dict, alias: str) -> str:
    return _sum_costs(board_cells(run), alias)


def run_total_cost(run: dict) -> str:
    """True cost of the whole run, candidate-sweep probes included."""
    return _sum_costs(run.get("cells") or [])


def load_pricing(root: Path) -> dict | None:
    """data/pricing.json as written by probe.pricing, or None when unusable."""
    try:
        payload = json.loads((root / "data" / "pricing.json").read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("routes"), dict):
        return None
    return payload


def load_catalog(root: Path) -> dict | None:
    """data/catalog.json as written by probe.catalog, or None when unusable."""
    try:
        payload = json.loads((root / "data" / "catalog.json").read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), dict):
        return None
    return payload


def load_intelligence(root: Path) -> dict | None:
    """data/intelligence.json as written by probe.intelligence, or None."""
    try:
        payload = json.loads((root / "data" / "intelligence.json").read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), dict):
        return None
    return payload


def aa_slug(route: str) -> str | None:
    """Route -> AA slug. Thin delegate — the logic lives in probe.registry
    (single copy, so the radar screens and the site can never diverge)."""
    return registry.aa_slug(route)


def ask_series(dated: list[tuple[str, dict]], route: str, current: dict | None = None) -> list[tuple[str, float, float]]:
    """(day, ask_in, ask_out) points for one route across dated snapshots.

    Days with no logged ask for the route are skipped (the sparkline plots
    known points, not gaps); the live snapshot is appended as the final
    point so today's ask always ends the line.
    """
    points: list[tuple[str, float, float]] = []
    for day, payload in dated:
        entry = (payload.get("routes") or {}).get(route) or {}
        a_in, a_out = entry.get("ask_in"), entry.get("ask_out")
        if a_in is not None and a_out is not None:
            points.append((day, float(a_in), float(a_out)))
    if current is not None:
        entry = (current.get("routes") or {}).get(route) or {}
        a_in, a_out = entry.get("ask_in"), entry.get("ask_out")
        day = ((current.get("generated_at") or "")[:10]) or "now"
        if a_in is not None and a_out is not None and (not points or points[-1][0] != day):
            points.append((day, float(a_in), float(a_out)))
    return points


def pricing_rows(payload: dict | None) -> list[dict]:
    """All routes with any billed rate, in the order the probe wrote them.

    Candidate-flagged entries WITH billed traffic stay in — the money view
    has no blind spot (a candidate can be actively billing); each carries
    candidate=True so the renderer can mark it.
    """
    if not payload:
        return []
    rows = []
    for route, entry in (payload.get("routes") or {}).items():
        if not isinstance(entry, dict):
            continue
        if entry.get("ask_in") is None and entry.get("eff_per_mtok") is None:
            continue
        if entry.get("candidate"):
            billed = entry.get("reqs") or 0
            if not billed or (entry.get("eff_per_mtok") is None
                              and entry.get("cost_usdc") is None):
                continue  # candidate with no real billing: candidates section only
            rows.append({"route": route, "candidate": True, **entry})
        else:
            rows.append({"route": route, **entry})
    return rows


def rate_label(raw: object, sig: int = 3, prefix: str = "$",
               fixed: int | None = None) -> str:
    """Compact $/M label: '$0.014', '$2.49'; extra decimals when tiny.

    fixed=N forces N decimal places (owner order 2026-09-07: all prices
    in a comparison surface must carry the same decimal count for visual
    scanning) — the caller chooses the count per surface; default keeps
    the compact sig-fig form.

    Thin delegate — the formatter lives in probe.pricing so the daily
    report and the site share one money format (review C3).
    """
    return pricing.rate_label(raw, sig=sig, fixed=fixed)


def token_label(count: int) -> str:
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.0f}k"
    return str(count)


def cache_label(raw: object) -> str:
    """68.7 -> '69%'; '' when absent."""
    try:
        return f"{float(str(raw)):.0f}%"
    except (TypeError, ValueError):
        return ""


# ── pricing-table visuals ──


def log_bar_pct(value: object, lo: float, hi: float) -> float:
    """Bar width % for value on a log10 scale between lo and hi, clamped.

    hi <= lo (a single non-zero peer) collapses to 100% for values at or
    above hi, so a one-row table still draws its bar.
    """
    try:
        v = float(str(value))
    except (TypeError, ValueError):
        return 0.0
    if v <= 0 or lo <= 0:
        return 0.0
    if hi <= lo:
        return 100.0 if v >= hi else 0.0
    pos = (math.log10(v) - math.log10(lo)) / (math.log10(hi) - math.log10(lo))
    return max(0.0, min(100.0, pos * 100.0))


def peer_bounds(values) -> tuple[float, float]:
    """(min, max) of the non-zero peers, or (0.0, 0.0) when there are none."""
    nonzero = [float(v) for v in values if v and float(v) > 0]
    if not nonzero:
        return (0.0, 0.0)
    return (min(nonzero), max(nonzero))


def rate_color_class(raw: object) -> str:
    """Bar color for an effective $/M rate: teal healthy, amber above.

    Color law: red is reserved for failures — an expensive route is a
    warning (amber), never a failure.
    """
    try:
        v = float(str(raw))
    except (TypeError, ValueError):
        return ""
    if v <= 0.02:
        return "ok"
    return "mid"


def cache_color_class(raw: object) -> str:
    """Bar color for a cache-hit percentage: teal healthy, amber below."""
    try:
        v = float(str(raw))
    except (TypeError, ValueError):
        return ""
    if v >= 70:
        return "ok"
    return "mid"


def cache_bar_pct(raw: object) -> float:
    """Cache percentage clamped to a 0-100 bar width."""
    try:
        return max(0.0, min(100.0, float(str(raw))))
    except (TypeError, ValueError):
        return 0.0


# ── spend dashboard: dated snapshots, day series, ask deltas ──


def load_dated_pricing(root: Path) -> list[tuple[str, dict]]:
    """data/pricing/*.json as (YYYY-MM-DD, payload), oldest first; skips broken files."""
    directory = root / "data" / "pricing"
    if not directory.is_dir():
        return []
    dated: list[tuple[str, dict]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(payload, dict) or not isinstance(payload.get("routes"), dict):
            continue
        dated.append((path.stem, payload))
    return dated


def prior_pricing(dated: list[tuple[str, dict]], current: dict | None) -> dict | None:
    """The most recent dated snapshot strictly before the current snapshot's day."""
    cur_day = ((current or {}).get("generated_at") or "")[:10]
    best: dict | None = None
    for day, payload in dated:
        if cur_day and day >= cur_day:
            continue
        best = payload  # dated arrives oldest-first, so the last match wins
    return best


def spend_days(payload: dict | None) -> list[dict]:
    """The snapshot's per-UTC-day spend series, validated down to usable entries."""
    days = (payload or {}).get("days")
    if not isinstance(days, list):
        return []
    return [d for d in days if isinstance(d, dict) and d.get("date")]


def day_cost(day: dict) -> float:
    try:
        return float(str(day.get("cost_usdc")))
    except (TypeError, ValueError):
        return 0.0


def spend_between(days: list[dict], start: str, end: str) -> float:
    """Total cost over entries whose date falls inside [start, end] inclusive."""
    return sum(day_cost(d) for d in days if start <= (d.get("date") or "") <= end)


def probe_spend(runs: list[dict]) -> float:
    """All-time cost of probe runs, board and candidate cells alike."""
    total = 0.0
    for run in runs:
        for cell in run.get("cells") or []:
            try:
                total += float(cell.get("cost_usdc") or 0)
            except (TypeError, ValueError):
                continue
    return total


def ask_deltas(current: dict | None, prior: dict | None, route: str) -> dict | None:
    """Ask-rate movement vs the prior dated snapshot: {'in': Δ, 'out': Δ}.

    None when there is no honest comparison — no prior snapshot, the route
    missing on either side, or a billed rate missing on either side.
    """
    if not prior:
        return None
    cur = ((current or {}).get("routes") or {}).get(route) or {}
    prev = (prior.get("routes") or {}).get(route) or {}
    deltas: dict[str, float] = {}
    for key, field in (("in", "ask_in"), ("out", "ask_out")):
        try:
            deltas[key] = float(str(cur.get(field))) - float(str(prev.get(field)))
        except (TypeError, ValueError):
            return None
    return deltas


def month_day_label(date: str) -> str:
    """'2026-08-21' -> 'Aug 21'; '' on malformed input."""
    months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    parts = date.split("-")
    if len(parts) != 3:
        return ""
    try:
        return f"{months[int(parts[1]) - 1]} {int(parts[2])}"
    except (IndexError, ValueError):
        return ""


def run_groups(run: dict) -> list[dict]:
    """Model groups [{model, routes}] from a run's sweep.

    Every probed family appears — board-only families (no audition routes)
    with an empty route list — so the section renders the in-use verdict
    even when the market shortlist found nothing cheaper.

    Cells are the primary source (order = first appearance). Routes listed
    in the run's shortlist without cells — a sweep aborted early — still
    get a row under their own family, so they render as unprobed instead
    of disappearing.

    (Moved here from generate.py in the P2b section-module carve: it is
    run-data shaping, not rendering.)
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
    for cell in candidate_cells(run):
        add(cell.get("model") or "", cell.get("alias") or "")
    for route in run.get("candidates") or []:
        route = str(route)
        add(market.family(route), route)
    return [{"model": model, "routes": routes_by_model[model]} for model in order]
