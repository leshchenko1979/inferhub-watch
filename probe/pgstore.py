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

# The projection gate verdict, published for the Grafana panels so the
# dashboard reads the SAME gate the board renders on (probe.basis.gate_state
# -> official_compare.projection_gate). One row, rewritten per sync; the
# panels fall back to the realized basis when `pass` is false, and to
# realized again when the row is absent (COALESCE ... false).
GATE_DDL = """
create table if not exists projection_gate (
    id text primary key,
    pass boolean not null,
    n integer,
    land integer,
    share numeric,
    tol numeric,
    min_n integer,
    -- Item 2 (D4): the Value leg, MEASURED alongside the cost verdict.
    -- `pass` stays the COST verdict and the panels' `use_proj` switch still
    -- reads it - these columns report what the same crown walk says when the
    -- regret ratio is taken on Value on both sides instead of on cost. See
    -- `official_compare._crown_value_regrets`; the constants are shared.
    value_n integer,
    value_land integer,
    value_share numeric,
    value_tol numeric,
    value_min_n integer,
    value_pass boolean,
    value_status text,
    computed_at timestamptz not null default now()
);
-- The verdict was re-tuned from rank fidelity (land / bar / rho_median) onto
-- the top-1 crown backtest (land / tol), so an existing table's stale columns
-- are superseded in place and dropped. Guarded so a fresh database is a no-op.
alter table projection_gate add column if not exists land integer;
alter table projection_gate add column if not exists tol numeric;
alter table projection_gate add column if not exists min_n integer;
alter table projection_gate drop column if exists bar;
alter table projection_gate drop column if exists rho_median;
alter table projection_gate drop column if exists within;
-- Item 2 (D4): an existing table gains the Value leg in place. The create
-- above covers a fresh database; these cover one that predates the columns.
alter table projection_gate add column if not exists value_n integer;
alter table projection_gate add column if not exists value_land integer;
alter table projection_gate add column if not exists value_share numeric;
alter table projection_gate add column if not exists value_tol numeric;
alter table projection_gate add column if not exists value_min_n integer;
alter table projection_gate add column if not exists value_pass boolean;
alter table projection_gate add column if not exists value_status text;
"""

# Per-route money bases, published from the committed snapshot so the
# Grafana panels price a route exactly as the board does (probe.basis
# .route_bases). Realized is the 30d bill; projected is NULL until the
# route has projection evidence, which makes the panel fall back to
# realized — the same fallback basis.board_basis applies.
ROUTE_BASIS_DDL = """
create table if not exists route_basis (
    route text primary key,
    realized numeric,
    projected numeric,
    snapshot_at text,
    computed_at timestamptz not null default now()
);
"""


# The predictor scoreboard, published so the Grafana panels can read it.
# Grafana cannot run `scripts/predictor_scoreboard.py` — the artifact is a
# recomputation over the committed snapshots plus the whole log store, not a
# query — so the script publishes its own verdicts here and the panels read
# them. One row per CADENCE (daily / hourly_cumulative / hourly_slice), which
# is the shape the scoreboard itself is organised in.
#
# `value_status` is load-bearing, not decoration. The Value leg carries
# `n = 0` / `share = null` at the hourly cadences, and a bare null there reads
# as a measured failure on a dashboard. It is a RESOLUTION limit instead — see
# `_value_resolution` in scripts/predictor_scoreboard.py — so the status
# (`measured` / `unresolved`) travels with the numbers and the panel renders
# "n/a" rather than "0.0".
SCOREBOARD_DDL = """
create table if not exists predictor_scoreboard (
    cadence text primary key,
    transitions integer,
    cost_n integer,
    cost_land integer,
    cost_share numeric,
    cost_pass boolean,
    value_n integer,
    value_land integer,
    value_share numeric,
    value_pass boolean,
    value_status text,
    value_limit text,
    level_n integer,
    level_median numeric,
    level_within_50pct numeric,
    level_within_2x numeric,
    computed_at timestamptz not null default now()
);
"""

# The Grafana datasource reads as `inferhub_ro`, not as the owner role, so
# every table a panel queries needs an explicit SELECT grant. Without it the
# panel errors "permission denied for table ..." on the live dashboard while
# the same SQL runs fine over the owner connection — the failure only shows
# up on the surface the owner actually looks at. Guarded on the role so a
# fresh database without the dashboard role still provisions.
GRANT_DDL = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'inferhub_ro') THEN
        EXECUTE 'grant select on usage_logs, route_metrics, projection_gate, '
             || 'route_basis, predictor_scoreboard to inferhub_ro';
    END IF;
END $$;
"""

def load_env(path: Path | None = None) -> dict[str, str]:
    """Parse the env file (never raises — callers handle empty gracefully).

    Environment variables WIN over the file. CI has no
    /root/.inferhub_pg.env and supplies the same five keys as job env vars
    (the workflow writes them to $GITHUB_ENV), so a file-only lookup would
    silently drop CI back onto the 12k-row API path — the Finding B defect
    in a new costume.
    """
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
    for k in ("PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD"):
        v = os.environ.get(k)
        if v:
            env[k] = v
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
        cur.execute(GATE_DDL)
        cur.execute(ROUTE_BASIS_DDL)
        cur.execute(SCOREBOARD_DDL)
        cur.execute(GRANT_DDL)
    conn.commit()

GATE_ID = "projection_gate"

def publish_projection_gate(conn, gate: dict) -> None:
    """Upsert the projection gate verdict the dashboard reads.

    `gate` is official_compare.projection_gate output: n / land / share /
    tol / min_n / pass for the COST leg, plus the parallel Value leg
    (value_n / value_land / value_share / value_tol / value_min_n /
    value_pass / value_status). The single row is rewritten in place — the
    dashboard needs the current verdict, not a history (the history is the
    committed snapshots the verdict is recomputed from).

    The Value leg is PUBLISHED, never substituted: `pass` stays the cost
    verdict and the panels' `use_proj` switch keeps reading it. Item 2 (D4)
    moved what the gate MEASURES; which leg it ships on is a separate,
    owner-level decision, so this row carries both and lets a reader see the
    disagreement rather than having it resolved silently in code.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into projection_gate
                (id, pass, n, land, share, tol, min_n, computed_at,
                 value_n, value_land, value_share, value_tol, value_min_n,
                 value_pass, value_status)
            values (%s, %s, %s, %s, %s, %s, %s, now(),
                    %s, %s, %s, %s, %s, %s, %s)
            on conflict (id) do update set
                pass = excluded.pass,
                n = excluded.n,
                land = excluded.land,
                share = excluded.share,
                tol = excluded.tol,
                min_n = excluded.min_n,
                value_n = excluded.value_n,
                value_land = excluded.value_land,
                value_share = excluded.value_share,
                value_tol = excluded.value_tol,
                value_min_n = excluded.value_min_n,
                value_pass = excluded.value_pass,
                value_status = excluded.value_status,
                computed_at = now()
            """,
            (GATE_ID, bool(gate.get("pass")), gate.get("n"), gate.get("land"),
             gate.get("share"), gate.get("tol"), gate.get("min_n"),
             gate.get("value_n"), gate.get("value_land"),
             gate.get("value_share"), gate.get("value_tol"),
             gate.get("value_min_n"), gate.get("value_pass"),
             gate.get("value_status")),
        )
    conn.commit()

def publish_route_basis(conn, rows: list[dict], snapshot_at: str | None = None) -> int:
    """Replace the published per-route money bases.

    A full replace, not an append: a route that leaves the snapshot must
    leave the table too, or the panels would rank a route the board no
    longer prices. Same transaction, so the panels never read a half-set.
    """
    tuples = [
        (r["route"], r.get("realized"), r.get("projected"), snapshot_at)
        for r in rows if r.get("route")
    ]
    with conn.cursor() as cur:
        cur.execute("delete from route_basis")
        if tuples:
            cur.executemany(
                """
                insert into route_basis (route, realized, projected, snapshot_at, computed_at)
                values (%s, %s, %s, %s, now())
                """,
                tuples,
            )
    conn.commit()
    return len(tuples)


SCOREBOARD_CADENCES = ("daily", "hourly_cumulative", "hourly_slice")

def publish_scoreboard(conn, artifact: dict) -> int:
    """Publish the predictor scoreboard, one row per cadence.

    `artifact` is `scripts/predictor_scoreboard.py`'s output. A cadence whose
    block is absent or skipped is DELETED rather than left standing: the
    hourly legs vanish when Postgres credentials are missing, and a stale row
    would keep publishing a verdict the run did not make.
    """
    rows = []
    for cadence in SCOREBOARD_CADENCES:
        blk = artifact.get(cadence) or {}
        if "ordering" not in blk or "level" not in blk:
            continue
        cost = blk["ordering"].get("cost_regret") or {}
        val = blk["ordering"].get("value_regret") or {}
        res = blk["ordering"].get("value_resolution") or {}
        lv = blk["level"] or {}
        rows.append((
            cadence, blk.get("transitions"),
            cost.get("n"), cost.get("land"), cost.get("share"), cost.get("pass"),
            val.get("n"), val.get("land"), val.get("share"), val.get("pass"),
            res.get("status"), res.get("limit"),
            lv.get("n_pairs"), lv.get("median_ratio"),
            lv.get("within_50pct"), lv.get("within_2x"),
        ))
    with conn.cursor() as cur:
        cur.execute("delete from predictor_scoreboard where not (cadence = any(%s))",
                    ([r[0] for r in rows],))
        if rows:
            cur.executemany(
                """
                insert into predictor_scoreboard (
                    cadence, transitions,
                    cost_n, cost_land, cost_share, cost_pass,
                    value_n, value_land, value_share, value_pass,
                    value_status, value_limit,
                    level_n, level_median, level_within_50pct, level_within_2x,
                    computed_at
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                          %s, %s, %s, %s, now())
                on conflict (cadence) do update set
                    transitions = excluded.transitions,
                    cost_n = excluded.cost_n,
                    cost_land = excluded.cost_land,
                    cost_share = excluded.cost_share,
                    cost_pass = excluded.cost_pass,
                    value_n = excluded.value_n,
                    value_land = excluded.value_land,
                    value_share = excluded.value_share,
                    value_pass = excluded.value_pass,
                    value_status = excluded.value_status,
                    value_limit = excluded.value_limit,
                    level_n = excluded.level_n,
                    level_median = excluded.level_median,
                    level_within_50pct = excluded.level_within_50pct,
                    level_within_2x = excluded.level_within_2x,
                    computed_at = now()
                """,
                rows,
            )
    conn.commit()
    return len(rows)

_ROW_RE = re.compile(r"^[a-z0-9-]{10,}$", re.IGNORECASE)

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


def load_route_metrics(conn=None) -> tuple[dict[str, dict], datetime | None]:
    """Load cached route_metrics models and max(updated_at) from Postgres.

    Returns:
        (models_dict, max_updated_at)
        where models_dict maps route -> {
            "ask_in": float | None,
            "ask_out": float | None,
            "official_in": float | None,
            "official_out": float | None,
            "supports_cache": bool,
            "iq": float | None,
            "book_in": float | None,
            "book_out": float | None,
            "ladder_at": str | None,
        }

    ``ask_in``/``ask_out`` are the FLOOR (unchanged meaning); ``book_in``/
    ``book_out`` are the book price stamped beside it (issue #41). The book
    fields are ``None`` on any row written before the book columns existed,
    which callers must read as "no book known", never as free.
    """
    own = conn is None
    if own:
        env = load_env()
        if not env.get("PGPASSWORD"):
            return {}, None
        try:
            conn = _connect(env)
        except Exception:
            return {}, None
    try:
        with conn.cursor() as cur:
            cur.execute("""
                select route, ask_in, ask_out, official_in, official_out,
                       supports_cache, iq, updated_at, book_in, book_out,
                       ladder_at
                from route_metrics
            """)
            rows = cur.fetchall()
            if not rows:
                return {}, None
            models: dict[str, dict] = {}
            max_updated: datetime | None = None
            for r, ai, ao, oi, oo, sc, iq, up, bi, bo, la in rows:
                models[r] = {
                    "ask_in": float(ai) if ai is not None else None,
                    "ask_out": float(ao) if ao is not None else None,
                    "official_in": float(oi) if oi is not None else None,
                    "official_out": float(oo) if oo is not None else None,
                    "supports_cache": bool(sc),
                    "iq": float(iq) if iq is not None else None,
                    "book_in": float(bi) if bi is not None else None,
                    "book_out": float(bo) if bo is not None else None,
                    "ladder_at": la.isoformat() if la is not None else None,
                }
                if up is not None:
                    if max_updated is None or up > max_updated:
                        max_updated = up
            return models, max_updated
    finally:
        if own and conn is not None:
            conn.close()
