#!/usr/bin/env python3
"""Backfill / sync usage_logs from the Management API into Postgres.

Run locally (the agents box) — CI has no DB access, so pgstore caching is
a local-only concern. Idempotent: upsert on request_id. Fetches only rows
newer than the newest cached ts (minus an overlap window) to stay under
the API rate limit.

Usage: python3 scripts/sync_usage_logs.py [--days N]
"""

from __future__ import annotations

import argparse
import difflib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import tomllib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from probe import pgstore
from probe.costs import fetch_log_rows, parse_ts

KEYS_FILE = Path("/root/.opencrabs/profiles/ops/keys.toml")
OVERLAP = timedelta(minutes=30)


def api_key() -> str:
    with KEYS_FILE.open("rb") as f:
        key = tomllib.load(f)["providers"]["custom"]["inferhub"]["api_key"]
    if not key:
        raise SystemExit("no inferhub api key in keys.toml")
    return key


def _dedupe(rows: list[dict]) -> list[dict]:
    """API pagination can return the same request id twice; ON CONFLICT
    DO UPDATE cannot affect the same row twice in one statement."""
    seen: set[str] = set()
    out: list[dict] = []
    for r in rows:
        rid = str(r.get("id") or r.get("request_id") or "")
        if rid in seen:
            continue
        seen.add(rid)
        out.append(r)
    return out


def tag_probe_windows(conn) -> int:
    """Flag rows inside known probe-run windows (data/runs/*.json)."""
    import glob
    import json

    windows = []
    for fp in sorted(glob.glob(str(Path(__file__).resolve().parents[1]
                                   / "data" / "runs" / "*.json"))):
        with open(fp) as f:
            r = json.load(f)
        s, fin = r.get("started_at"), r.get("finished_at")
        if not s or not fin:
            continue
        start = parse_ts(s) - timedelta(minutes=2)
        end = parse_ts(fin) + timedelta(minutes=2)
        windows.append((start, end, tuple(r.get("aliases") or [])))

    tagged = 0
    for start, end, aliases in windows:
        with conn.cursor() as cur:
            if aliases:
                cur.execute(
                    "update usage_logs set is_probe = true"
                    " where not is_probe and created_at between %s and %s"
                    " and model = any(%s)",
                    (start, end, list(aliases)))
            else:
                cur.execute(
                    "update usage_logs set is_probe = true"
                    " where not is_probe and created_at between %s and %s",
                    (start, end))
            tagged += cur.rowcount
    return tagged


def _norm_slug(name: str) -> str:
    """Normalize a model tail to intelligence.json slug style: lowercase,
    underscores/dots -> dashes."""
    return name.strip().lower().replace("_", "-").replace(".", "-")


def _pick_candidate(cands: list[str], slugs: dict) -> str | None:
    """Choose among slug candidates: prefer one that actually carries an
    iq score, then the shortest (closest to the base model), then
    lexically for determinism."""
    with_iq = [s for s in cands if (slugs.get(s) or {}).get("iq") is not None]
    pool = with_iq or cands
    return min(pool, key=lambda s: (len(s), s))


def _digit_tokens(name: str) -> set[str]:
    """Tokens that carry a digit ('v2', '4', '1m') — the version identity of
    a model name. Used to stop the fuzzy fallback from crossing versions."""
    return {t for t in name.split("-") if any(c.isdigit() for c in t)}


def resolve_slug(route: str, aa_map: dict, slugs: dict) -> str | None:
    """Map a catalog route (publisher/model or publisher/vendor/model) to an
    intelligence.json model slug.

    Order (issue #6):
      1. models.toml [aa] override — explicit, wins over everything.
      2. Exact normalized tail (dots/underscores -> dashes).
      3. A known slug extends the tail ('kimi-k2-7' -> 'kimi-k2-7-code',
         'mimo-v2-5' -> 'mimo-v2-5-pro').
      4. A known slug is a prefix of the tail — the route appended a
         qualifier, context tag or date ('claude-opus-4-7-1m' ->
         'claude-opus-4-7', 'gemini-3-6-flash-high' -> 'gemini-3-6-flash',
         'deepseek-v4-flash-0731' -> 'deepseek-v4-flash'). Longest match
         wins — it is the most specific model.
      5. Word-order-insensitive index ('claude-haiku-4-5' route tail vs
         'claude-4-5-haiku' slug).
      6. Fuzzy (difflib, 0.75 cutoff) as a last resort, guarded so a
         candidate can never drop a version token — 'claude-opus-4-7-1m'
         must not fuzzy to 'claude-opus-5' (difflib rates it 0.77); it is
         resolved by (4) instead.
    """
    tail = route.rsplit("/", 1)[-1]
    direct = aa_map.get(route) or _norm_slug(tail)
    if direct in slugs:
        return direct

    parts = direct.split("-")
    # the tail is a prefix of a known slug: kimi-k2-7 -> kimi-k2-7-code
    extension = [s for s in slugs if s.startswith(direct + "-")]
    if extension:
        return _pick_candidate(extension, slugs)
    # a known slug is a prefix of the tail: claude-opus-4-7-1m ->
    # claude-opus-4-7, gemini-3-6-flash-high -> gemini-3-6-flash
    for i in range(len(parts) - 1, 0, -1):
        cand = "-".join(parts[:i])
        if cand in slugs:
            return cand
    # word-order-insensitive: claude-haiku-4-5 -> claude-4-5-haiku
    key = "-".join(sorted(parts))
    swapped = [s for s in slugs
               if "-".join(sorted(s.split("-"))) == key and s != direct]
    if swapped:
        return _pick_candidate(swapped, slugs)
    # fuzzy last resort, version-guarded (see docstring step 6)
    close = difflib.get_close_matches(direct, list(slugs), n=5, cutoff=0.75)
    close = [s for s in close if _digit_tokens(direct) <= _digit_tokens(s)]
    if close:
        return _pick_candidate(close, slugs)
    return None


def sync_route_metrics(conn, live_models: dict[str, dict] | None = None) -> int:
    """D3 decision layer: upsert catalog asks + AA IQ per route into
    route_metrics, the table the dashboard's scatter/panels join against."""
    import json

    root = Path(__file__).resolve().parents[1]
    intel = json.loads((root / "data" / "intelligence.json").read_text())
    toml_models = tomllib.loads((root / "models.toml").read_text()) \
        if (root / "models.toml").exists() else {}
    aa_map = toml_models.get("aa") or {}
    slugs = intel.get("models") or {}

    if live_models is not None:
        models = live_models
    else:
        catalog = json.loads((root / "data" / "catalog.json").read_text())
        models = catalog.get("models") or {}

    with conn.cursor() as cur:
        cur.execute("""
            create table if not exists route_metrics (
                route text primary key,
                ask_in numeric(12,6),
                ask_out numeric(12,6),
                official_in numeric(12,6),
                official_out numeric(12,6),
                supports_cache boolean,
                iq numeric(8,2),
                updated_at timestamptz not null default now()
            )
        """)
        n = 0
        for route, m in models.items():
            slug = resolve_slug(route, aa_map, slugs)
            iq = (slugs.get(slug) or {}).get("iq") if slug else None
            cur.execute("""
                insert into route_metrics
                    (route, ask_in, ask_out, official_in, official_out,
                     supports_cache, iq, updated_at)
                values (%s, %s, %s, %s, %s, %s, %s, now())
                on conflict (route) do update set
                    ask_in = excluded.ask_in,
                    ask_out = excluded.ask_out,
                    official_in = excluded.official_in,
                    official_out = excluded.official_out,
                    supports_cache = excluded.supports_cache,
                    iq = excluded.iq,
                    updated_at = now()
            """, (route, m.get("ask_in"), m.get("ask_out"),
                  m.get("official_in"), m.get("official_out"),
                  m.get("supports_cache"), iq))
            n += 1
    return n

def sync_projection_gate(conn) -> dict:
    """Recompute the projection gate from committed snapshots and publish it.

    The board computes the gate itself from data/pricing/*.json; the Grafana
    panels cannot (no template variables on public dashboards), so the same
    verdict is published to Postgres and read by their SQL. Same owner, one
    verdict — see probe.basis.gate_state.
    """
    from probe import basis

    gate = basis.gate_state()
    pgstore.publish_projection_gate(conn, gate)
    return gate

def sync_route_basis(conn) -> int:
    """Publish every route's money bases as the board's snapshot computes them.

    Grafana cannot read data/pricing.json, so without this the panels re-derive
    the basis from the billed store and rank a different route than the board
    (issue #15). Prices come from probe.basis — the single money-basis owner.
    """
    import json

    from probe import basis, pricing

    root = Path(__file__).resolve().parents[1]
    payload = json.loads((root / "data" / "pricing.json").read_text())
    dated = pricing.dated_snapshots(root)
    return pgstore.publish_route_basis(
        conn, basis.route_bases(payload, dated), snapshot_at=payload.get("generated_at")
    )

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=8.0,
                    help="how far back to fetch on an empty table")
    args = ap.parse_args()

    conn = pgstore._connect(pgstore.load_env())
    pgstore.ensure_schema(conn)
    with conn.cursor() as cur:
        cur.execute("select max(created_at) from usage_logs")
        newest = cur.fetchone()[0]

    now = datetime.now(timezone.utc)
    if newest is None:
        after = now - timedelta(days=args.days)
    else:
        newest = newest if newest.tzinfo else newest.replace(tzinfo=timezone.utc)
        after = newest - OVERLAP

    key = api_key()

    # Refresh live catalog asks in-memory for route_metrics and auto-route switch (issue #11)
    # Do not overwrite tracked data/catalog.json, which is maintained by daily GitHub Actions sweeps
    live_models = None
    try:
        from probe.catalog import fetch_models
        live_models = fetch_models(key)
        if live_models:
            print(f"catalog live asks fetched in-memory: {len(live_models)} models")
    except Exception as exc:
        print(f"catalog refresh warning: {exc}")

    print(f"syncing rows newer than {after.isoformat()} ...")
    rows = fetch_log_rows(key, range_="30d", after=after, max_pages=60,
                          pace_s=1.0)
    print(f"fetched {len(rows)} rows from api")

    # Trim to the window: pages are newest-first and can straddle the
    # cutoff; keep only rows newer than after minus a margin.
    cutoff = after - OVERLAP
    rows = [r for r in rows if parse_ts(r["ts"]) > cutoff]
    if rows:
        cached = pgstore.upsert_rows(_dedupe(rows), conn=conn)
        print(f"upserted {cached} rows")
        tagged = tag_probe_windows(conn)
        print(f"tagged {tagged} probe rows")
    else:
        print("no new usage logs to cache")

    metrics = sync_route_metrics(conn, live_models=live_models)
    print(f"route_metrics upserted: {metrics}")
    gate = sync_projection_gate(conn)
    print(f"projection gate published: pass={gate.get('pass')} "
          f"{gate.get('land')}/{gate.get('n')} transitions landed at "
          f"rho >= {gate.get('bar')} (median {gate.get('rho_median')})")
    print(f"route basis published: {sync_route_basis(conn)} routes")
    with conn.cursor() as cur:
        cur.execute("select max(created_at) from usage_logs")
        print(f"table now ends at {cur.fetchone()[0]}")
    conn.commit()

    # Trigger automated route switching evaluation (issue #12)
    try:
        from scripts.auto_route_switch import run_auto_route_switch
        switch_res = run_auto_route_switch(catalog_models=live_models)
        if switch_res.get("should_switch"):
            print(f"auto_route_switch: switched to {switch_res.get('best_model')}")
        else:
            print(f"auto_route_switch: no switch needed ({switch_res.get('reason')})")
    except Exception as exc:
        print(f"auto_route_switch warning: {exc}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
