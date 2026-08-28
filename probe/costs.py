"""Actual probe cost, read back from the Management API usage logs.

Completions return usage tokens but never a price field, so after a run we
pull /usage/logs and match each cell to its log row: same model (the alias),
a timestamp inside the run window and — when the cell recorded usage —
identical prompt/completion token counts. A matched row carries the billed
cost_consumer_usdc. Attribution is best-effort: cells that cannot be matched
safely are left without a cost rather than guessed.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

MANAGEMENT = "https://inferhub.dev/api"
PAGE_SIZE = 100
MAX_PAGES = 40
USER_AGENT = "inferhub-watch/1.0"
# Legacy runs (before finished_at existed) stamp only the end time; a full
# probe takes at most a few minutes, so scan 35 minutes back.
LEGACY_WINDOW = timedelta(minutes=35)
MARGIN = timedelta(seconds=60)


def parse_ts(raw: str) -> datetime:
    """ISO-8601 UTC — '2026-08-27T19:52:20.015Z' or run 'started_at'."""
    return datetime.fromisoformat((raw or "").replace("Z", "+00:00"))


RATE_LIMIT_RETRIES = 5
RETRY_AFTER_DEFAULT_S = 15.0
RETRY_AFTER_CAP_S = 60.0


def _retry_after_s(exc: urllib.error.HTTPError) -> float:
    """The server's Retry-After in seconds, clamped; a default when absent."""
    raw = ""
    if exc.headers is not None:
        raw = exc.headers.get("Retry-After") or ""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return RETRY_AFTER_DEFAULT_S
    return min(max(value, 1.0), RETRY_AFTER_CAP_S)


def _get_json(url: str, key: str) -> dict:
    """GET one JSON page; on HTTP 429 wait out Retry-After and retry in place.

    The Management API rate-limits short request bursts (~12s windows); a
    paginated sweep trips it unless each 429 is respected where it happens.
    """
    for attempt in range(RATE_LIMIT_RETRIES):
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {key}",
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == RATE_LIMIT_RETRIES - 1:
                raise
            wait = _retry_after_s(exc)
            print(
                f"warning: rate limited on {url.split('?')[0]}; "
                f"retrying in {wait:.0f}s",
                file=sys.stderr,
            )
            time.sleep(wait)
    raise AssertionError("unreachable: the loop returns or raises")


def fetch_log_rows(
    key: str,
    *,
    range_: str = "24h",
    after: datetime | None = None,
    max_pages: int | None = None,
    pace_s: float = 0.0,
) -> list[dict]:
    """Paginate /usage/logs newest-first; stop once older than `after`.

    `max_pages` raises the cap for wide ranges (30d needs ~60 pages).
    `pace_s` spaces page fetches to stay under the rate limit; 429s are
    retried in place with the server's Retry-After either way.
    """
    cap = max_pages if max_pages is not None else MAX_PAGES
    rows: list[dict] = []
    page = 1
    while page <= cap:
        url = (
            f"{MANAGEMENT}/usage/logs?range={range_}&sort=ts&dir=desc"
            f"&pageSize={PAGE_SIZE}&page={page}"
        )
        body = _get_json(url, key)
        batch = body.get("rows") or []
        if not batch:
            break
        rows.extend(batch)
        if after is not None and parse_ts(batch[-1]["ts"]) < after:
            break
        total = int(body.get("rangeTotal") or 0)
        if page * PAGE_SIZE >= total:
            break
        page += 1
        if pace_s > 0:
            time.sleep(pace_s)
    return rows


def run_window(run: dict) -> tuple[datetime, datetime]:
    """(start, end) of the run's request window, with a small margin."""
    started_raw = run.get("started_at") or ""
    started = parse_ts(started_raw)
    finished_raw = run.get("finished_at") or ""
    if finished_raw:
        return started - MARGIN, parse_ts(finished_raw) + MARGIN
    return started - LEGACY_WINDOW, started + MARGIN


def cell_token_triples(cell: dict) -> list[tuple[int, int]]:
    """(prompt, completion) for every request the cell sent.

    Most checks send one request; cache_tools sends up to three (usage_all).
    """
    evidence = cell.get("evidence") or {}
    triples: list[tuple[int, int]] = []
    for usage in evidence.get("usage_all") or [evidence.get("usage") or {}]:
        prompt = usage.get("prompt_tokens") if isinstance(usage, dict) else None
        completion = usage.get("completion_tokens") if isinstance(usage, dict) else None
        if isinstance(prompt, int) and isinstance(completion, int):
            triples.append((prompt, completion))
    return triples


def _tag(cell: dict, row: dict, match: str) -> None:
    cell["cost_usdc"] = str(row.get("cost_consumer_usdc") or "")
    cell["cost_match"] = match


def attribute_costs(
    run: dict, rows: list[dict], alias_models: dict[str, list[str]] | None = None
) -> dict:
    """Attach cost_usdc to cells in a run payload. Returns a small summary.

    `alias_models` maps an alias to extra log-model strings to pool (legacy
    runs requested bare ids while logs recorded the resolved route, e.g.
    'glm-5.3' -> 'cb/glm-5.3').
    """
    start, end = run_window(run)
    by_model: dict[str, list[tuple[datetime, dict]]] = {}
    for row in rows:
        ts = parse_ts(row.get("ts") or "")
        if start <= ts <= end:
            by_model.setdefault(row.get("model") or "", []).append((ts, row))
    for pool in by_model.values():
        pool.sort(key=lambda pair: pair[0])

    def pool_for(alias: str) -> list[tuple[datetime, dict]]:
        names = [alias] + ((alias_models or {}).get(alias) or [])
        merged: list[tuple[datetime, dict]] = []
        for name in names:
            merged.extend(by_model.get(name, []))
        merged.sort(key=lambda pair: pair[0])
        return merged

    checks_order = run.get("checks") or []
    rank = {check_id: i for i, check_id in enumerate(checks_order)}
    matched = 0
    total = 0.0
    for alias in run.get("aliases") or []:
        pool = pool_for(alias)
        cells = sorted(
            (c for c in run.get("cells") or [] if c.get("alias") == alias),
            key=lambda c: rank.get(c.get("check_id") or "", 1 << 30),
        )
        unclaimed = [True] * len(pool)
        pending: list[dict] = []
        for cell in cells:
            spent = 0.0
            hits = 0
            for toks in cell_token_triples(cell):
                for i, (_, row) in enumerate(pool):
                    if not unclaimed[i]:
                        continue
                    row_toks = (row.get("prompt_tokens"), row.get("completion_tokens"))
                    if row_toks == toks:
                        unclaimed[i] = False
                        try:
                            spent += float(row.get("cost_consumer_usdc") or "")
                        except ValueError:
                            pass
                        hits += 1
                        break
            if hits:
                cell["cost_usdc"] = f"{spent:.6f}"
                cell["cost_match"] = "tokens"
            else:
                pending.append(cell)
        # Weak pass: requests are sequential, so remaining cells and remaining
        # rows align in time order — but only when nothing else shares the
        # window (row count equals cell count), else attribution is a guess.
        if pending and len(pool) == len(cells):
            free = [i for i, ok in enumerate(unclaimed) if ok]
            if len(free) == len(pending):
                for cell, i in zip(pending, free):
                    _tag(cell, pool[i][1], "order")
        for cell in cells:
            if "cost_usdc" in cell:
                matched += 1
                try:
                    total += float(cell["cost_usdc"])
                except ValueError:
                    pass
    return {
        "matched": matched,
        "cells": len(run.get("cells") or []),
        "total_usdc": f"{total:.6f}",
        "source": "usage-logs",
    }
