"""Postgres usage-log cache: nightly upsert of raw /usage/logs rows.

The Management API caps pagination at 12,000 rows (MAX_PAGES x PAGE_SIZE) —
at current traffic a true 30-day window is ~190k rows, so the 30d aggregate
silently covers ~2 days of data. This module stores the RAW rows in the
`telemetry` Postgres on apps (database inferhub_logs) so pricing aggregates
over the real window. Probe-owned requests are tagged is_probe at insert so
query-time filters can exclude them.

Connection: SSH tunnel localhost:15432 -> apps:5432 (container postgres),
credentials in /root/.inferhub_pg.env (chmod 600, gitignored host file).
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path

ENV_FILE = Path("/root/.inferhub_pg.env")
_TUNNEL_PID: int | None = None

DDL = """
create table if not exists usage_logs (
    request_id text primary key,
    created_at timestamptz not null,
    model text not null,
    prompt_tokens bigint default 0,
    completion_tokens bigint default 0,
    cached_tokens bigint default 0,
    ttft_ms bigint,
    duration_ms bigint,
    ask_input_per_mtok numeric(12,6),
    ask_output_per_mtok numeric(12,6),
    cost_usdc numeric(12,6),
    is_probe boolean not null default false,
    inserted_at timestamptz not null default now()
);
create index if not exists idx_ul_created on usage_logs (created_at);
create index if not exists idx_ul_model on usage_logs (model, created_at);
"""


def load_env(path: Path | None = None) -> dict[str, str]:
    """Parse the env file (never raises — callers handle empty gracefully)."""
    env: dict[str, str] = {}
    p = path or ENV_FILE
    try:
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    except OSError:
        pass
    return env


def _connect(env: dict[str, str]):
    import psycopg2

    return psycopg2.connect(
        host=env.get("PGHOST", "127.0.0.1"),
        port=int(env.get("PGPORT", "15432")),
        dbname=env.get("PGDATABASE", "inferhub_logs"),
        user=env.get("PGUSER", "postgres"),
        password=env.get("PGPASSWORD", ""),
        connect_timeout=5,
    )


def ensure_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(DDL)
    conn.commit()


_ROW_RE = re.compile(r"^[a-z0-9-]{10,}$", re.I)


def row_fields(row: dict) -> tuple | None:
    """(request_id, created_at, model, tokens..., is_probe) from a log row."""
    rid = str(row.get("id") or row.get("request_id") or "")
    if not _ROW_RE.match(rid):
        return None
    ts = str(row.get("ts") or row.get("created_at") or "")
    if not ts:
        return None

    def num(key: str) -> float | None:
        v = row.get(key)
        if v in (None, ""):
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def val(key):
        v = row.get(key)
        return v if v not in ("", None) else None

    return (
        rid,
        ts,
        str(row.get("model") or ""),
        int(row.get("prompt_tokens") or 0),
        int(row.get("completion_tokens") or 0),
        int(row.get("cached_tokens") or 0),
        val("ttft_ms"),
        val("duration_ms"),
        num("ask_input_per_mtok"),
        num("ask_output_per_mtok"),
        num("cost_consumer_usdc"),
    )


def upsert_rows(rows: list[dict], conn=None) -> int:
    """Insert-or-update raw log rows; returns rows written. Never raises on
    individual-row problems (skips malformed, counts the rest)."""
    own = conn is None
    if own:
        env = load_env()
        if not env.get("PGPASSWORD"):
            return 0
        conn = _connect(env)
    try:
        from psycopg2.extras import execute_values

        ensure_schema(conn)
        tuples = []
        for row in rows:
            fields = row_fields(row)
            if fields is not None:
                tuples.append(fields + (False,))
        if not tuples:
            return 0
        with conn.cursor() as cur:
            execute_values(
                cur,
                """
                insert into usage_logs (request_id, created_at, model,
                    prompt_tokens, completion_tokens, cached_tokens,
                    ttft_ms, duration_ms, ask_input_per_mtok,
                    ask_output_per_mtok, cost_usdc, is_probe)
                values %s
                on conflict (request_id) do update set
                    ttft_ms = excluded.ttft_ms,
                    duration_ms = excluded.duration_ms,
                    cost_usdc = excluded.cost_usdc
                """,
                tuples,
                page_size=500,
            )
        conn.commit()
        return len(tuples)
    finally:
        if own:
            conn.close()


def latest_ts(conn=None) -> datetime | None:
    """Newest created_at in the cache (None = empty)."""
    own = conn is None
    if own:
        env = load_env()
        if not env.get("PGPASSWORD"):
            return None
        conn = _connect(env)
    try:
        with conn.cursor() as cur:
            cur.execute("select max(created_at) from usage_logs")
            got = cur.fetchone()[0]
            return got
    finally:
        if own:
            conn.close()


def rows_since(since: datetime, exclude_probes: bool = True, conn=None) -> list[dict]:
    """Raw rows newer than `since`, shaped like /usage/logs rows so the
    existing aggregators (aggregate_rows, perf_stats, ...) work unchanged."""
    own = conn is None
    if own:
        env = load_env()
        if not env.get("PGPASSWORD"):
            return []
        conn = _connect(env)
    try:
        sql = """
            select request_id, created_at, model, prompt_tokens,
                   completion_tokens, cached_tokens, ttft_ms, duration_ms,
                   ask_input_per_mtok, ask_output_per_mtok, cost_usdc, is_probe
            from usage_logs where created_at > %s"""
        if exclude_probes:
            sql += " and not is_probe"
        with conn.cursor() as cur:
            cur.execute(sql + " order by created_at", (since,))
            out = []
            for (rid, ts, model, tin, tout, cached, ttft, dur,
                 ask_in, ask_out, cost, _probe) in cur.fetchall():
                out.append({
                    "id": rid,
                    "ts": ts.isoformat() if ts else "",
                    "model": model,
                    "prompt_tokens": tin,
                    "completion_tokens": tout,
                    "cached_tokens": cached,
                    "ttft_ms": ttft,
                    "duration_ms": dur,
                    "ask_input_per_mtok": ask_in,
                    "ask_output_per_mtok": ask_out,
                    "cost_consumer_usdc": cost,
                })
            return out
    finally:
        if own:
            conn.close()


def window_rows(window_hours: int, exclude_probes: bool = True, conn=None) -> list[dict]:
    """Convenience: rows from the last N hours."""
    since = datetime.now(timezone.utc) - __import__("datetime").timedelta(hours=window_hours)
    return rows_since(since, exclude_probes=exclude_probes, conn=conn)
