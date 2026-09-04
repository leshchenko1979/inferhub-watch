"""Cost per M tokens per provider+model, from the Management API.

Two sources, both real billing rather than estimates:

- /usage/logs rows carry ask_input_per_mtok / ask_output_per_mtok — the
  per-M rates actually billed on each request — plus token counts and
  cost_consumer_usdc. Aggregated over 30d per model (the route string) they
  give the ask rates in use (median over the recent window — robust to
  single-row outliers), the effective $/M over all tokens (cache
  discounts included) and the cache-hit share.
- /catalog carries each publisher's asks per model; used as the fallback
  for routes with no log rows yet.

Writes data/pricing.json for the site generator. Transient failures (an
HTTP 429 right after a probe run is the known one) are retried with backoff.
If every attempt fails the cron still survives: main() logs a warning,
emits a GitHub Actions annotation, exits 0, and leaves the previous file.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from statistics import median
from pathlib import Path

from probe.costs import MANAGEMENT, USER_AGENT, fetch_log_rows
from probe.registry import load_aliases, repo_root

CATALOG_TIMEOUT = 30
RANGE = "30d"
# The forward view's ask is the median over this many days of billed rows,
# not the last row: a single outlier request must not move the projection.
ASK_MEDIAN_DAYS = 7
# 30d is ~5.7k rows; fetch_log_rows caps pages at 40 by default.
MAX_PAGES = 120


def _get(url: str, key: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(req, timeout=CATALOG_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _cheapest_point(points: object) -> float | None:
    """Cheapest price in a pricePoints histogram ([[price, count], ...]).

    count>0 means at least one upstream provider offers the model at that
    price; the minimum such price is the cheapest ask. Non-numeric or
    empty entries are ignored.
    """
    if not isinstance(points, list):
        return None
    prices: list[float] = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        price, count = point[0], point[1]
        if isinstance(price, (int, float)) and price >= 0 and count:
            prices.append(float(price))
    return min(prices) if prices else None


def _model_asks(model: dict) -> tuple[float, float] | None:
    """(cheapest askIn, cheapest askOut) under either catalog schema.

    Legacy: asksIn/asksOut per-provider ask arrays. Current (observed
    2026-09-04): officialIn/Out plus pricePointsIn/Out histograms of
    [price, provider_count] — the cheapest ask is the lowest priced
    point. Returns None when either side has no usable price.
    """
    legacy_in = model.get("asksIn") or []
    legacy_out = model.get("asksOut") or []
    try:
        pair = (min(legacy_in), min(legacy_out))
    except (TypeError, ValueError):
        pair = None
    if pair is not None and all(isinstance(v, (int, float)) for v in pair):
        return pair
    cheap_in = _cheapest_point(model.get("pricePointsIn"))
    cheap_out = _cheapest_point(model.get("pricePointsOut"))
    if cheap_in is None or cheap_out is None:
        return None
    return cheap_in, cheap_out


def fetch_catalog(key: str) -> dict[str, tuple[float, float]]:
    """Map 'prefix/upstreamModelId' -> (cheapest askIn, cheapest askOut).

    An EMPTY result is never normal — the live catalog carries 150+ models.
    It means the API schema changed again (as on 2026-09-04, when asksIn/
    asksOut were replaced by pricePoints histograms and the candidate radar
    went blind silently). Warn loudly so blindness is visible immediately."""
    body = _get(f"{MANAGEMENT}/catalog", key)
    entries = body if isinstance(body, list) else body.get("rows") or []
    asks: dict[str, tuple[float, float]] = {}
    for entry in entries:
        prefix = entry.get("prefix") or ""
        if not prefix or not entry.get("enabled"):
            continue
        for model in entry.get("models") or []:
            if not model.get("enabled") or model.get("modelDisabled"):
                continue
            name = model.get("upstreamModelId") or ""
            pair = _model_asks(model)
            if name and pair and all(v >= 0 for v in pair):
                asks[f"{prefix}/{name}"] = pair
    if not asks:
        print(
            "WARNING: fetch_catalog returned 0 routes with live asks — "
            "catalog API schema likely changed; candidate radar is blind. "
            f"Raw entries seen: {len(entries)}",
            file=sys.stderr,
        )
    return asks


def _float(raw: object) -> float | None:
    try:
        value = float(str(raw))
    except (TypeError, ValueError):
        return None
    return value


def aggregate_rows(rows: list[dict]) -> dict[str, dict]:
    """Per model string: traffic totals and the median billed ask rates."""
    stats: dict[str, dict] = {}
    asks: dict[str, list[tuple[str, float, float]]] = {}
    for row in sorted(rows, key=lambda r: r.get("ts") or ""):
        model = row.get("model") or ""
        if not model:
            continue
        agg = stats.setdefault(
            model,
            {
                "reqs": 0,
                "tok_in": 0,
                "tok_out": 0,
                "cached": 0,
                "cost": 0.0,
                "ask_in": None,
                "ask_out": None,
                "last_ts": "",
            },
        )
        agg["reqs"] += 1
        agg["tok_in"] += int(row.get("prompt_tokens") or 0)
        agg["tok_out"] += int(row.get("completion_tokens") or 0)
        agg["cached"] += int(row.get("cached_tokens") or 0)
        cost = _float(row.get("cost_consumer_usdc"))
        if cost is not None:
            agg["cost"] += cost
        ask_in = _float(row.get("ask_input_per_mtok"))
        ask_out = _float(row.get("ask_output_per_mtok"))
        if ask_in is not None and ask_out is not None:
            asks.setdefault(model, []).append((row.get("ts") or "", ask_in, ask_out))
        agg["last_ts"] = row.get("ts") or agg["last_ts"]
    for model, pairs in asks.items():
        stats[model]["ask_in"], stats[model]["ask_out"] = _median_ask(pairs)
    return stats


def _parse_ts(raw: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def _median_ask(
    pairs: list[tuple[str, float, float]],
) -> tuple[float | None, float | None]:
    """Median billed (ask_in, ask_out) over the recent sub-window of rows.

    The last row's ask is a sample of one — a single outlier request used
    to move the whole forward view between adjacent days (backtest
    2026-09-01: ds-flash projected 0.0070 -> 0.0280 on one request). The
    median over the newest ASK_MEDIAN_DAYS of billed rows is robust to
    that; a route with no asks inside the sub-window (quiet route) falls
    back to the median of its whole 30d window, so the number only moves
    when real asks move.
    """
    if not pairs:
        return None, None
    pool = [(a_in, a_out) for _, a_in, a_out in pairs]
    newest = _parse_ts(pairs[-1][0])  # rows arrive ts-sorted, so last is newest
    if newest is not None:
        cutoff = newest - timedelta(days=ASK_MEDIAN_DAYS)
        recent = [
            (a_in, a_out)
            for ts, a_in, a_out in pairs
            if (parsed := _parse_ts(ts)) is not None and parsed >= cutoff
        ]
        if recent:
            pool = recent
    ins = sorted(a_in for a_in, _ in pool)
    outs = sorted(a_out for _, a_out in pool)
    return (
        float(median(ins)) if ins else None,
        float(median(outs)) if outs else None,
    )


def daily_series(rows: list[dict]) -> list[dict]:
    """Per-UTC-day spend and request totals over the fetched rows, oldest first."""
    per_day: dict[str, dict] = {}
    for row in rows:
        day = (row.get("ts") or "")[:10]
        if not day:
            continue
        agg = per_day.setdefault(day, {"cost": 0.0, "requests": 0})
        agg["requests"] += 1
        cost = _float(row.get("cost_consumer_usdc"))
        if cost is not None:
            agg["cost"] += cost
    return [
        {
            "date": day,
            "cost_usdc": f"{per_day[day]['cost']:.6f}",
            "requests": per_day[day]["requests"],
        }
        for day in sorted(per_day)
    ]


def failure_stats(rows: list[dict]) -> dict:
    """Failure counts and http-code breakdown over the fetched rows.

    Usage-log rows carry `status` ("ok" / "failed") and `http_status`.
    Failed rows carry no tokens and zero cost, so they never skew the
    price math — but they are real reliability data: every failed row
    here is a request the router accepted and the upstream dropped.
    """
    total = len(rows)
    failed = 0
    codes: dict[str, int] = {}
    by_model: dict[str, dict] = {}
    for row in rows:
        model = row.get("model") or ""
        if not model:
            continue
        m = by_model.setdefault(model, {"reqs": 0, "failed": 0, "codes": {}})
        m["reqs"] += 1
        if row.get("status") == "ok":
            continue
        failed += 1
        m["failed"] += 1
        code = str(row.get("http_status") or "unknown")
        codes[code] = codes.get(code, 0) + 1
        m["codes"][code] = m["codes"].get(code, 0) + 1
    return {
        "total": total,
        "failed": failed,
        "rate_pct": round(failed / total * 100, 2) if total else None,
        "codes": dict(sorted(codes.items(), key=lambda kv: -kv[1])),
        "by_model": dict(sorted(by_model.items(), key=lambda kv: -kv[1]["failed"])),
    }


def route_entry(stats: dict | None, catalog: dict, alias: str, *, candidate: bool = False) -> dict:
    """One route as it lands in pricing.json — logs first, catalog fallback."""
    if not stats:
        ask_in, ask_out = catalog.get(alias) or (None, None)
        entry = {
            "ask_in": ask_in,
            "ask_out": ask_out,
            "eff_per_mtok": None,
            "cache_pct": None,
            "reqs": 0,
            "tok_in": 0,
            "tok_out": 0,
            "cost_usdc": None,
            "last_ts": None,
            "hit_ask_ratio": None,
            "source": "catalog" if ask_in is not None else "none",
        }
    else:
        toks = stats["tok_in"] + stats["tok_out"]
        eff = stats["cost"] / toks * 1e6 if toks else None
        cache_pct = stats["cached"] / stats["tok_in"] * 100 if stats["tok_in"] else None
        entry = {
            "ask_in": stats["ask_in"],
            "ask_out": stats["ask_out"],
            "eff_per_mtok": round(eff, 4) if eff is not None else None,
            "cache_pct": round(cache_pct, 1) if cache_pct is not None else None,
            "reqs": stats["reqs"],
            "tok_in": stats["tok_in"],
            "tok_out": stats["tok_out"],
            "cost_usdc": f'{stats["cost"]:.6f}',
            "last_ts": stats["last_ts"],
            "hit_ask_ratio": round(stats["hit_ask_ratio"], 4)
            if stats.get("hit_ask_ratio") is not None else None,
            "source": "usage-logs",
        }
    if candidate:
        entry["candidate"] = True
    return entry


def prior_snapshot_cutoff(root: Path | None = None) -> str | None:
    """generated_at of the latest dated snapshot strictly before today.

    The marginal-realized comparator counts usage rows newer than this
    cutoff; None (no earlier snapshot, unreadable file) means everything
    in the window is marginal.
    """
    root = root or repo_root()
    today = datetime.now(timezone.utc).date().isoformat()
    best: Path | None = None
    for path in sorted((root / "data" / "pricing").glob("*.json")):
        if path.stem < today:
            best = path  # sorted, so the last pre-today file wins
    if best is None:
        return None
    try:
        payload = json.loads(best.read_text())
    except (OSError, ValueError):
        return None
    ts = payload.get("generated_at")
    return str(ts) if ts else None


MARGINAL_TS_CAP = 400  # per-route ts list cap; beyond this the route is
# traffic-heavy by definition and never probe-only


def marginal_stats(rows: list[dict], cutoff: str | None) -> dict[str, dict]:
    """Per model: traffic + cost over usage rows strictly after the cutoff.

    The marginal realized $/M over this slice is the fair forward
    comparator for the projection gate - realized-over-30d smears price
    changes across the whole window, this one does not. Also keeps the
    request timestamps (capped at MARGINAL_TS_CAP) so the board can tell
    probe-only traffic (every request inside a sweep window) from real
    working traffic.
    """
    out: dict[str, dict] = {}
    for row in rows:
        ts = str(row.get("ts") or "")
        if cutoff and ts <= cutoff:
            continue
        model = row.get("model") or ""
        if not model:
            continue
        m = out.setdefault(
            model,
            {"reqs": 0, "tok_in": 0, "tok_out": 0, "cost": 0.0, "ts": []},
        )
        m["reqs"] += 1
        if len(m["ts"]) < MARGINAL_TS_CAP:
            m["ts"].append(ts)
        m["tok_in"] += int(row.get("prompt_tokens") or 0)
        m["tok_out"] += int(row.get("completion_tokens") or 0)
        cost = _float(row.get("cost_consumer_usdc"))
        if cost is not None:
            m["cost"] += cost
    return out


def snapshot(key: str, aliases: list[str], range_: str = RANGE,
             candidates: list[str] | None = None) -> dict:
    """Build the full pricing payload; candidate routes are flagged as such."""
    rows = fetch_log_rows(key, range_=range_, max_pages=MAX_PAGES, pace_s=0.25)
    stats = aggregate_rows(rows)
    catalog = fetch_catalog(key)
    cutoff = prior_snapshot_cutoff()
    marginal = marginal_stats(rows, cutoff)
    cand = set(candidates or [])

    from probe.official_compare import cache_rule_stats

    def _entry(alias: str) -> dict:
        st = stats.get(alias)
        entry_stats = None
        if st:
            entry_stats = dict(st)
            entry_stats["hit_ask_ratio"] = cache_rule_stats(rows, alias)
        entry = route_entry(entry_stats, catalog, alias, candidate=alias in cand)
        # no prior snapshot -> no cutoff -> the whole window would count as
        # "marginal", which is just realized again: leave the keys off
        m = marginal.get(alias) if cutoff else None
        if m and (m["tok_in"] + m["tok_out"]):
            toks = m["tok_in"] + m["tok_out"]
            entry["marginal_per_mtok"] = round(m["cost"] / toks * 1e6, 4)
            entry["marginal_reqs"] = m["reqs"]
            entry["marginal_since"] = cutoff
            entry["marginal_ts"] = m["ts"]
            entry["marginal_ts_truncated"] = m["reqs"] > len(m["ts"])
        return entry

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "range": range_,
        "requests_scanned": len(rows),
        "days": daily_series(rows),
        "failures": failure_stats(rows),
        "routes": {alias: _entry(alias) for alias in aliases},
    }


def latest_run_candidates(root: Path | None = None) -> list[str]:
    """Candidate routes from the newest committed run file (may be empty).

    Runs write their shortlist under "candidates"; legacy runs without the
    key fall back to the candidate-flagged cells.
    """
    root = root or repo_root()
    files = sorted((root / "data" / "runs").glob("*.json"))
    if not files:
        return []
    try:
        payload = json.loads(files[-1].read_text())
    except (OSError, ValueError):
        return []
    candidates = [str(r) for r in payload.get("candidates") or [] if str(r)]
    if candidates:
        return candidates
    seen: set[str] = set()
    out: list[str] = []
    for cell in payload.get("cells") or []:
        alias = cell.get("alias") or ""
        if cell.get("candidate") and alias and alias not in seen:
            seen.add(alias)
            out.append(alias)
    return out


def snapshot_routes() -> tuple[list[str], list[str]]:
    """(all routes for the snapshot, the candidate subset) — board first, deduped."""
    routes = list(load_aliases())
    cand: list[str] = []
    for route in latest_run_candidates():
        if route not in routes and route not in cand:
            cand.append(route)
    return routes + cand, cand


def write_outputs(payload: dict, root: Path | None = None) -> tuple[Path, Path]:
    """Write data/pricing.json plus the dated copy data/pricing/YYYY-MM-DD.json.

    Same-day re-runs overwrite that day's copy; history accumulates per day.
    """
    root = root or repo_root()
    text = json.dumps(payload, indent=2) + "\n"
    latest = root / "data" / "pricing.json"
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text(text)
    dated = root / "data" / "pricing" / f"{datetime.now(timezone.utc):%Y-%m-%d}.json"
    dated.parent.mkdir(parents=True, exist_ok=True)
    dated.write_text(text)
    return latest, dated


# A probe run burns rate budget; the pricing fetch right after it is the
# classic 429 window. Back off and retry before giving up.
ATTEMPTS = 3
RETRY_BACKOFF_S = 30


def main() -> int:  # noqa: BLE001 — pricing must never break the cron
    key = os.environ.get("INFERHUB_API_KEY", "").strip()
    if not key:
        print("INFERHUB_API_KEY is required", file=sys.stderr)
        return 0
    try:
        routes, cand = snapshot_routes()
    except Exception as exc:  # noqa: BLE001
        print(f"warning: could not load routes: {exc}", file=sys.stderr)
        return 0
    last_exc: Exception | None = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            payload = snapshot(key, routes, candidates=cand)
            latest, dated = write_outputs(payload)
            print(latest)
            print(dated)
            return 0
        except Exception as exc:  # noqa: BLE001 — e.g. HTTP Error 429
            last_exc = exc
            if attempt < ATTEMPTS:
                wait = RETRY_BACKOFF_S * attempt
                print(
                    f"warning: pricing attempt {attempt}/{ATTEMPTS} failed "
                    f"({exc}); retrying in {wait}s",
                    file=sys.stderr,
                )
                time.sleep(wait)
    print(
        f"warning: pricing snapshot failed after {ATTEMPTS} attempts, "
        f"keeping previous file: {last_exc}",
        file=sys.stderr,
    )
    # Surface the staleness in CI — silent success is how this went unnoticed.
    print(f"::warning::pricing snapshot failed ({last_exc}); data/pricing.json is STALE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
