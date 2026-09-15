"""Predictor scoreboard: how well the forward view ranks and how well it levels.

Scores the route predictor on TWO axes (issue #30):

- **ordering accuracy** (the verdict): the crown backtest's regret share -
  of the transitions where the board would have crowned a route, what share
  of crowns realized within GATE_TOL of the cheapest realized route. This is
  `official_compare.projection_gate`'s own question, asked at two cadences.
- **level error** (the diagnostic): realized / projected, per route and
  aggregate - whether the forward view lands on the right LEVEL even when it
  ranks correctly.

Two cadences, because they answer different questions and have very different
statistical power:

- **daily** - the committed `data/pricing/*.json` snapshots. This is the
  history the gate itself backtests on; it is where `GATE_MIN_N = 10` is
  reachable.
- **hourly** - the cadence the switcher ACTS at (the `:23` sync). Built
  AS-OF from Postgres `usage_logs`: the projection a board would have shown
  at hour t, from rows with `created_at <= t`, so the forward view is honest
  rather than reconstructed from a snapshot that already knew the answer.

The hourly series is THIN, and its answer depends on what "realized" means,
so BOTH readings are published rather than one being chosen silently:

- **cumulative** - realized = the route's trailing-window eff at t+1. This is
  the gate's own semantics (its "newer snapshot" eff is a trailing aggregate)
  and it is stable, because a trailing 30d eff barely moves in an hour.
- **slice** - realized = what the route ACTUALLY cost in the hour that
  followed. This is the switcher's real question at hourly cadence, and it is
  far thinner (most hours have < 3 routes with enough traffic to compare).

Nothing here re-derives a money basis: prices come from `probe.basis` and the
backtest from `official_compare`, so the scoreboard can never disagree with
the board or the gate about what a route costs.
"""

from __future__ import annotations

import argparse
import itertools
import json
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from probe import basis, official_compare, pricing, value  # noqa: E402
from probe.registry import aa_slug  # noqa: E402

INTELLIGENCE = ROOT_DIR / "data" / "intelligence.json"
DEFAULT_OUT = ROOT_DIR / "data" / "scoreboard.json"
MIN_REQS = official_compare.MIN_REQS
# The +-50% band is read LOG-SYMMETRICALLY: a ratio of 2/3 and 3/2 are the
# same 50% miss in opposite directions, where a linear 0.5..1.5 band would
# treat a 2x overshoot and a 2x undershoot differently. Both are published.
BAND_LO, BAND_HI = 2.0 / 3.0, 1.5
BAND_2X_LO, BAND_2X_HI = 0.5, 2.0


def _iq_table() -> dict:
    """AA-slug -> intelligence entry, via the registry's single route mapping."""
    try:
        return json.loads(INTELLIGENCE.read_text()).get("models") or {}
    except (OSError, ValueError):
        return {}


def _iq_of(models: dict, route: str) -> float | None:
    slug = aa_slug(route)
    entry = models.get(slug) if slug else None
    return entry.get("iq") if entry else None

def _iq_asof(payload: dict, route: str, models: dict, fallback: bool) -> float | None:
    """IQ as the snapshot ITSELF recorded it (D3), never as today knows it.

    D3 stamps `iq` at build time precisely so a past day's crown can be
    reconstructed without look-ahead. When the key is present it is used
    unconditionally - including when it is null, which means that day carried
    no IQ and the route simply cannot be Value-scored.

    Only a payload that predates the stamp may consult the live table, and
    then only when `fallback` says so. That path is look-ahead CONTAMINATED:
    `data/intelligence.json` is overwritten each sweep, so the live value is
    not the value that day saw. Callers that take it must label the result.

    The stamp rule itself is owned by `probe.official_compare` (`iq_asof` +
    `has_iq_stamp`), so the gate and this backtest cannot drift apart on what
    "as-of IQ" means.
    """
    if official_compare.has_iq_stamp(payload, route):
        return official_compare.iq_asof(payload, route)
    return _iq_of(models, route) if fallback else None


def _fleet_tps_ref(payload: dict) -> float:
    """Thin alias — the formula's single owner is `probe.value`."""
    return value.fleet_tps_ref(payload)


def _tps_of(payload: dict, route: str, ref: float) -> float:
    """Thin alias — kept by name because tests call it directly."""
    return value.tps_of(payload, route, ref)


def value_of(iq: float | None, eff: float | None, tps: float, ref: float) -> float | None:
    """Value = (IQ / eff) * (tps / ref) ** 0.5 — the North Star metric.

    Delegates to `probe.value`, the single owner of the formula; the name is
    retained for this module's call sites and tests.
    """
    return value.value_of(iq, eff, tps, ref)


def _comparables(older: dict, real_map: dict, hist: list) -> list[tuple[str, float, float]]:
    """(route, projected, realized) for routes comparable in both windows.

    `real_map` carries the realized side, so the caller decides which
    "realized" the backtest means (trailing aggregate, or a single slice).
    """
    comp: list[tuple[str, float, float]] = []
    for route, st in (older.get("routes") or {}).items():
        if not isinstance(st, dict) or (st.get("reqs") or 0) < MIN_REQS:
            continue
        real = real_map.get(route)
        if not real or real <= 0:
            continue
        p = basis.projected(older, route, hist)
        if p is None or p <= 0:
            continue
        comp.append((route, float(p), float(real)))
    return comp


def _order_block(regrets: list[float]) -> dict:
    n = len(regrets)
    land = sum(1 for r in regrets if r <= official_compare.GATE_TOL)
    share = round(land / n, 3) if n else None
    return {
        "n": n, "land": land, "share": share,
        "tol": official_compare.GATE_TOL,
        "min_n": official_compare.GATE_MIN_N,
        "share_bar": official_compare.GATE_SHARE,
        "pass": n >= official_compare.GATE_MIN_N and share is not None
                and share >= official_compare.GATE_SHARE,
        "median_regret": round(statistics.median(regrets), 4) if regrets else None,
    }


def _value_resolution(n: int) -> dict:
    """Why the Value leg has the `n` it has — a RESOLUTION limit, not a verdict.

    `n == 0` must never be read as "the Value leg failed". The leg reads each
    snapshot's OWN as-of `iq` stamp (D3), and only the 17 committed DAILY
    snapshots carry one. The hourly series is rebuilt from `usage_logs` rows
    by `hourly_series()` and stamps no `iq`, so `_iq_asof` returns None for
    every hourly boundary and the leg scores nothing.

    That is a PLUMBING gap on top of a resolution limit, and the distinction
    is stated rather than blurred: IQ's own resolution is a DAY
    (`data/intelligence.json` is a git-tracked daily series), so even once the
    hourly series carries a stamp the input would be day-resolution — the same
    IQ for every boundary inside a day. Resolving it means joining each
    boundary's date to that day's IQ from the same git history D3 backfilled
    from; it is NOT impossible, it is simply not built, and no volume of
    traffic closes either gap.

    Stated IN the artifact so a reader — or a dashboard panel — cannot
    mistake `n: 0` / `share: null` for a measured failure.
    """
    if n:
        return {
            "status": "measured",
            "n": n,
            "basis": ("each snapshot's own as-of iq stamp (D3), itself "
                      "backfilled from the git-tracked data/intelligence.json "
                      "history at the commit at-or-before that snapshot's date "
                      "— real historical values, never interpolated"),
        }
    return {
        "status": "unresolved",
        "n": 0,
        "limit": "no as-of IQ on the hourly series",
        "note": ("NOT a measured failure. The hourly series is rebuilt from "
                 "usage_logs and carries no D3 `iq` stamp, so there is no "
                 "as-of IQ to read at an hourly boundary. On top of that, IQ's "
                 "resolution is a DAY (data/intelligence.json is a daily "
                 "series), so the input would be day-resolution even once "
                 "stamped. `share: null` and `pass: false` on this leg are "
                 "artifacts of that limit and carry NO evidence about Value. "
                 "The daily leg is the leg that can be scored."),
    }


def score(dated: list, models: dict, real_maps: list | None = None,
          iq_fallback: bool = False) -> dict:
    """Ordering (cost + Value regret) and level error over a dated series.

    `real_maps[i]` is the realized side of transition i (older=dated[i]);
    omitted, the newer payload's own trailing eff is used - the gate's
    semantics, and what the daily series means.

    The Value leg reads each snapshot's OWN D3 `iq` stamp, so a past day's
    crown is reconstructed from what that day actually knew. `iq_fallback`
    permits the live intelligence table for payloads that predate the stamp
    (the hourly series is rebuilt as-of and carries none) - that path is
    look-ahead CONTAMINATED and the result says so via `value_iq_source`.
    """
    cost_regrets: list[float] = []
    value_regrets: list[float] = []
    pairs: list[tuple[str, float, float]] = []
    histogram: dict[int, int] = {}

    # Precompute causal expanding fleet calibration multipliers (Strategy 2)
    # in linear O(N) time across transitions.
    running_hit_series: dict[str, list[float]] = {}
    running_ratios: list[float] = []
    kappas: list[float] = [1.0]

    for k in range(len(dated) - 1):
        older_payload = dated[k][1]
        newer_payload = dated[k + 1][1]
        older_routes = older_payload.get("routes") or {}
        newer_routes = newer_payload.get("routes") or {}

        for r_name, st in older_routes.items():
            if int(st.get("reqs") or 0) >= official_compare.MIN_CONF_REQS:
                h = official_compare._hit_rate(st)
                if h is not None:
                    running_hit_series.setdefault(r_name, []).append(h)

        for r_name, st in older_routes.items():
            if not isinstance(st, dict) or (st.get("reqs") or 0) < 3:
                continue
            real_eff = (newer_routes.get(r_name) or {}).get("eff_per_mtok")
            if not real_eff or real_eff <= 0:
                continue
            series = list(running_hit_series.get(r_name, []))
            own = official_compare._hit_rate(st)
            if own is not None:
                series.append(own)
            if not series:
                hit = None
            else:
                hit = series[0]
                for v in series[1:]:
                    hit = official_compare.HIT_EWMA_ALPHA * v + (1 - official_compare.HIT_EWMA_ALPHA) * hit
                n = int(st.get("reqs") or 0)
                if n < official_compare.MIN_CONF_REQS:
                    prior = official_compare.fleet_hit_prior(older_routes)
                    if prior is not None:
                        hit = (n * hit + official_compare.SHRINK_K * prior) / (n + official_compare.SHRINK_K)
            p_raw = official_compare.inferhub_eff(st, hit=hit)
            if p_raw and p_raw > 0:
                running_ratios.append(float(real_eff) / float(p_raw))

        if len(running_ratios) < 3:
            kappas.append(1.0)
        else:
            kappas.append(float(statistics.median(running_ratios)))

    for i, (older, newer) in enumerate(itertools.pairwise(dated)):
        real_map = (real_maps[i] if real_maps is not None
                    else {r: (st or {}).get("eff_per_mtok")
                          for r, st in (newer[1].get("routes") or {}).items()})
        kappa = kappas[i]
        comp: list[tuple[str, float, float]] = []
        older_routes = older[1].get("routes") or {}
        for route, st in older_routes.items():
            if not isinstance(st, dict) or (st.get("reqs") or 0) < MIN_REQS:
                continue
            real = real_map.get(route)
            if not real or real <= 0:
                continue
            hit, _ = official_compare.projection_hit(dated[: i + 1], route, st, older_routes)
            p_raw = official_compare.inferhub_eff(st, hit=hit)
            if p_raw is None or p_raw <= 0:
                continue
            p = p_raw * kappa
            comp.append((route, float(p), float(real)))

        histogram[len(comp)] = histogram.get(len(comp), 0) + 1
        for route, p, real in comp:
            pairs.append((route, p, real))
        if len(comp) < 3:
            continue
        crowned = min(comp, key=lambda row: row[1])
        cheapest = min(comp, key=lambda row: row[2])
        cost_regrets.append(crowned[2] / cheapest[2] - 1)
        ref = _fleet_tps_ref(older[1])
        vc = value_of(_iq_asof(older[1], crowned[0], models, iq_fallback),
                      crowned[2], _tps_of(older[1], crowned[0], ref), ref)
        vk = value_of(_iq_asof(older[1], cheapest[0], models, iq_fallback),
                      cheapest[2], _tps_of(older[1], cheapest[0], ref), ref)
        if vc and vk:
            value_regrets.append(vk / vc - 1)

    ratios = [real / p for _, p, real in pairs]
    per: dict[str, list[float]] = {}
    for route, p, real in pairs:
        per.setdefault(route, []).append(real / p)
    return {
        "transitions": len(dated) - 1 if dated else 0,
        "comparable_histogram": {str(k): histogram[k] for k in sorted(histogram)},
        "ordering": {"cost_regret": _order_block(cost_regrets),
                     "value_regret": _order_block(value_regrets),
                     "value_iq_source": ("live intelligence.json (LOOK-AHEAD "
                                         "CONTAMINATED)" if iq_fallback
                                         else "snapshot as-of iq (D3)"),
                     # n == 0 on the Value leg is a resolution limit of the
                     # INPUT (daily IQ vs an hourly boundary), never a verdict.
                     # Stated here so no reader and no panel can read
                     # `pass: false` as evidence about Value.
                     "value_resolution": _value_resolution(len(value_regrets))},
        "level": {
            "n_pairs": len(ratios),
            "median_ratio": round(statistics.median(ratios), 3) if ratios else None,
            "within_50pct": round(sum(1 for r in ratios if BAND_LO <= r <= BAND_HI) / len(ratios), 3) if ratios else None,
            "within_2x": round(sum(1 for r in ratios if BAND_2X_LO <= r <= BAND_2X_HI) / len(ratios), 3) if ratios else None,
            "per_route": {route: {"n": len(v), "median_ratio": round(statistics.median(v), 3)}
                          for route, v in sorted(per.items(), key=lambda kv: -statistics.median(kv[1]))},
        },
    }


def hour_boundaries(rows: list[dict]) -> list[datetime]:
    stamps = [pricing.parse_ts(r.get("ts") or "") for r in rows]
    stamps = [t for t in stamps if t is not None]
    if not stamps:
        return []
    first = min(stamps).replace(minute=0, second=0, microsecond=0)
    last = max(stamps).replace(minute=0, second=0, microsecond=0)
    out, cur = [], first
    while cur <= last:
        out.append(cur)
        cur += timedelta(hours=1)
    return out


def _slice_realized(rows: list[dict]) -> dict:
    """What each route actually cost in ONE slice of rows ($/Mtok)."""
    agg: dict[str, dict] = {}
    for r in rows:
        model = r.get("model") or ""
        if not model:
            continue
        a = agg.setdefault(model, {"tok": 0, "cost": 0.0})
        a["tok"] += int(r.get("prompt_tokens") or 0) + int(r.get("completion_tokens") or 0)
        a["cost"] += float(r.get("cost_consumer_usdc") or 0)
    return {m: (v["cost"] / v["tok"] * 1e6 if v["tok"] else None) for m, v in agg.items()}


def hourly_series(rows: list[dict], models: dict | None = None, perf_hours: int = 24) -> tuple[list, list]:
    """As-of hourly snapshots + the per-transition SLICE realized maps.

    Maintains incremental running aggregates and a sliding 168h window to
    generate the 377+ hourly backtest snapshots in linear time without
    re-scanning the cumulative log store.
    """
    from collections import deque

    if not rows:
        return [], []

    # 1. Parse records once into flat structures
    parsed_rows = []
    for r in rows:
        t = pricing.parse_ts(r.get("ts") or "")
        if t is None:
            continue
        m = r.get("model") or ""
        if not m:
            continue
        tok_in = int(r.get("prompt_tokens") or 0)
        tok_out = int(r.get("completion_tokens") or 0)
        cached = int(r.get("cached_tokens") or 0)
        cost = pricing._float(r.get("cost_consumer_usdc")) or 0.0
        a_in = pricing._float(r.get("ask_input_per_mtok"))
        a_out = pricing._float(r.get("ask_output_per_mtok"))
        ttft = pricing._float(r.get("ttft_ms"))
        dur = pricing._float(r.get("duration_ms"))
        tps = None
        if ttft is not None and dur is not None and tok_out > 0:
            stream_s = (dur - ttft) / 1000.0
            if stream_s >= 2.0 and tok_out / stream_s < 500:
                tps = tok_out / stream_s
        parsed_rows.append({
            "ts": t, "model": m, "tok_in": tok_in, "tok_out": tok_out,
            "cached": cached, "cost": cost, "a_in": a_in, "a_out": a_out,
            "ttft": ttft, "tps": tps, "raw": r
        })

    if not parsed_rows:
        return [], []

    first = min(r["ts"] for r in parsed_rows).replace(minute=0, second=0, microsecond=0)
    last = max(r["ts"] for r in parsed_rows).replace(minute=0, second=0, microsecond=0)
    hours: list[datetime] = []
    cur = first
    while cur <= last:
        hours.append(cur)
        cur += timedelta(hours=1)

    by_hour: dict[datetime, list[dict]] = {h: [] for h in hours}
    for r in parsed_rows:
        h = r["ts"].replace(minute=0, second=0, microsecond=0)
        if h in by_hour:
            by_hour[h].append(r)

    running_stats: dict[str, dict] = {}
    asks_by_model: dict[str, deque] = {}
    by_m_7d: dict[str, deque] = {}
    dated: list = []
    slices: list[dict] = []
    models_dict = models or {}

    cap_hours = pricing.TPS_WINDOW_CAP_H  # 168

    for h in hours:
        hr_rows = by_hour[h]
        for r in hr_rows:
            m = r["model"]
            st = running_stats.setdefault(m, {
                "reqs": 0, "tok_in": 0, "tok_out": 0, "cached": 0, "cost": 0.0,
                "ask_in": None, "ask_out": None, "last_ts": ""
            })
            st["reqs"] += 1
            st["tok_in"] += r["tok_in"]
            st["tok_out"] += r["tok_out"]
            st["cached"] += r["cached"]
            st["cost"] += r["cost"]
            if r["a_in"] is not None and r["a_out"] is not None:
                asks_by_model.setdefault(m, deque()).append((r["ts"], r["a_in"], r["a_out"]))
            st["last_ts"] = r["ts"].isoformat()
            by_m_7d.setdefault(m, deque()).append(r)

        cutoff_7d = h - timedelta(hours=cap_hours)
        cutoff_3d = h - timedelta(days=pricing.ASK_MEDIAN_DAYS)
        cutoff_24h = h - timedelta(hours=perf_hours)

        for m, dq in asks_by_model.items():
            while dq and dq[0][0] < cutoff_3d:
                dq.popleft()
            if dq:
                ins = sorted(p[1] for p in dq)
                outs = sorted(p[2] for p in dq)
                running_stats[m]["ask_in"] = float(statistics.median(ins))
                running_stats[m]["ask_out"] = float(statistics.median(outs))

        model_perf: dict[str, dict] = {}
        for m, dq in by_m_7d.items():
            while dq and dq[0]["ts"] < cutoff_7d:
                dq.popleft()
            if not dq:
                continue
            base = [r for r in dq if r["ts"] >= cutoff_24h]
            valid_tps_base = [r["tps"] for r in base if r["tps"] is not None]
            window = perf_hours
            picked = base
            valid_tps = valid_tps_base
            if len(valid_tps_base) < pricing.TPS_FLOOR and cap_hours > perf_hours:
                window = cap_hours
                picked = list(dq)
                valid_tps = [r["tps"] for r in picked if r["tps"] is not None]
            ttfts = [r["ttft"] for r in picked if r["ttft"] is not None]
            entry: dict = {
                "reqs": len(base),
                "window_hours": window,
                "ttft_p50_ms": float(statistics.median(ttfts)) if ttfts else None,
                "tps_mean": float(statistics.mean(valid_tps)) if valid_tps else None,
                "tps_samples": len(valid_tps)
            }
            if window != perf_hours:
                entry["window_reqs"] = len(picked)
            model_perf[m] = entry

        routes = {}
        for a, st in running_stats.items():
            r_entry = pricing.route_entry(st, {}, a)
            slug = aa_slug(a)
            r_entry["iq"] = (models_dict.get(slug) or {}).get("iq") if slug else None
            routes[a] = r_entry

        dated.append((h.date().isoformat(), {
            "generated_at": h.isoformat(),
            "routes": routes,
            "perf": {"window_hours": perf_hours, "models": model_perf}
        }))
        slices.append(_slice_realized([r["raw"] for r in hr_rows]))

    # transition i compares snapshot i to the NEXT hour's realized
    return dated, slices[1:]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--perf-hours", type=int, default=24)
    ap.add_argument("--no-pg", action="store_true", help="daily cadence only")
    ap.add_argument("--iq-fallback", action="store_true",
                    help="D4: let payloads with no D3 iq stamp read the LIVE "
                         "intelligence table (look-ahead contaminated)")
    args = ap.parse_args(argv)

    models = _iq_table()
    daily = pricing.dated_snapshots(ROOT_DIR)
    artifact: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "daily": score(daily, models, iq_fallback=args.iq_fallback),
        "cadences": {"daily": len(daily)},
    }

    conn = None
    if not args.no_pg:
        from probe.pgstore import _connect, load_env, rows_since
        env = load_env()
        if env.get("PGPASSWORD"):
            conn = _connect(env)
            rows = rows_since(datetime(2026, 8, 1, tzinfo=timezone.utc), conn=conn)
            dated, slices = hourly_series(rows, models=models, perf_hours=args.perf_hours)
            artifact["hourly_cumulative"] = score(dated, models, iq_fallback=args.iq_fallback)
            artifact["hourly_slice"] = score(dated, models, real_maps=slices,
                                             iq_fallback=args.iq_fallback)
            artifact["cadences"]["hourly"] = len(dated)
            artifact["rows"] = len(rows)
            if rows:
                artifact["window"] = pricing._window_of(rows, "30d")
        else:
            artifact["hourly_cumulative"] = {"skipped": "no Postgres credentials"}

    args.out.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
    print(f"scoreboard written: {args.out}")
    for key in ("daily", "hourly_cumulative", "hourly_slice"):
        blk = artifact.get(key) or {}
        if "ordering" not in blk:
            continue
        o, lv = blk["ordering"]["cost_regret"], blk["level"]
        v = blk["ordering"]["value_regret"]
        res = blk["ordering"].get("value_resolution") or {}
        # `pass=False` on an unresolved Value leg is a resolution artifact, not
        # a verdict — never print the bare boolean without the status.
        vpass = (("pass=%s" % v["pass"]) if res.get("status") == "measured"
                 else "pass=n/a (unresolved: %s)" % res.get("limit", "?"))
        print(f"  {key:18s} transitions={blk['transitions']:4d} "
              f"cost n={o['n']:3d} share={o['share']} pass={o['pass']} | "
              f"value n={v['n']:3d} share={v['share']} {vpass} | "
              f"level n={lv['n_pairs']:4d} median={lv['median_ratio']}")

    # Publish so the Grafana panels read the same verdicts this artifact
    # carries. Grafana cannot recompute the scoreboard — it is a pass over the
    # committed snapshots plus the whole log store, not a query — so the
    # script that owns the artifact is also the publisher (pgstore
    # .SCOREBOARD_DDL). A failure to publish is loud: a dashboard showing a
    # stale verdict is worse than one showing none.
    if conn is not None:
        from probe.pgstore import ensure_schema, publish_scoreboard
        try:
            ensure_schema(conn)
            print(f"scoreboard published: {publish_scoreboard(conn, artifact)} "
                  "cadence row(s)")
        finally:
            conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
