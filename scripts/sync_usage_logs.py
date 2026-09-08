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
    print(f"syncing rows newer than {after.isoformat()} ...")
    rows = fetch_log_rows(key, range_="30d", after=after, max_pages=60,
                          pace_s=1.0)
    print(f"fetched {len(rows)} rows from api")

    # Trim to the window: pages are newest-first and can straddle the
    # cutoff; keep only rows newer than after minus a margin.
    cutoff = after - OVERLAP
    rows = [r for r in rows if parse_ts(r["ts"]) > cutoff]
    if not rows:
        print("nothing to cache")
        return 0

    cached = pgstore.upsert_rows(_dedupe(rows), conn=conn)
    print(f"upserted {cached} rows")
    tagged = tag_probe_windows(conn)
    print(f"tagged {tagged} probe rows")
    with conn.cursor() as cur:
        cur.execute("select max(created_at) from usage_logs")
        print(f"table now ends at {cur.fetchone()[0]}")
    conn.commit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
