from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from probe import market
from probe.http import InferHubClient
from probe.payloads import URL
from probe.registry import (
    load_aliases,
    load_check_module,
    load_registry,
    repo_root,
)
from probe.result import result

BALANCE_MARKERS = ("balance too low", "insufficient_balance")


class BalanceTooLow(RuntimeError):
    """InferHub reported an out-of-balance error; the probe must abort."""


RETRY_WAIT_SECONDS = 20
RETRYABLE_HTTP_STATUSES = frozenset(range(500, 600))


def _attempt(
    module, client, alias: str, check_id: str
) -> tuple[dict | None, Exception | None]:
    """One probe attempt. Transport-level errors come back as (None, exc)
    instead of blowing up the loop; a balance abort still raises."""
    try:
        return module.run(client, alias), None
    except Exception as exc:  # noqa: BLE001
        if balance_too_low(str(exc)):
            raise BalanceTooLow(f"{alias}/{check_id}: {exc}") from exc
        return None, exc


def _retryable(cell: dict | None, exc: Exception | None) -> bool:
    """Timeout (transport error) or 5xx — transient congestion, worth one
    replay. Real assertion fails (status 'fail') are never retried: those
    say something about the endpoint, not the window."""
    if exc is not None:
        return True
    if cell is None or cell.get("status") != "error":
        return False
    status = cell.get("http_status")
    return isinstance(status, int) and status in RETRYABLE_HTTP_STATUSES


def balance_too_low(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in BALANCE_MARKERS)


def collect_cells(
    client: InferHubClient, aliases: list[str], registry: list[dict]
) -> tuple[list[dict], list[str]]:
    cells = []
    errors = []
    for alias in aliases:
        for i, spec in enumerate(registry):
            module = load_check_module(spec["id"])
            cell, exc = _attempt(module, client, alias, spec["id"])
            # One retry after a short wait: a timeout or 5xx in a congested
            # window (the 2026-09-01 inferhub degradation) says nothing about
            # the endpoint. Recovery is recorded on the cell as evidence —
            # "recovered on retry", not silently green.
            flaky_from: str | None = None
            if _retryable(cell, exc):
                flaky_from = str(exc) if exc is not None else str(cell.get("summary"))
                time.sleep(RETRY_WAIT_SECONDS)
                cell, exc = _attempt(module, client, alias, spec["id"])
            if exc is not None:
                errors.append(f"{alias}/{spec['id']}: {exc}")
                cell = result(
                    check_id=spec["id"],
                    alias=alias,
                    status="error",
                    summary=str(exc),
                )
            if flaky_from is not None:
                if exc is None:
                    cell["flaky_recovered"] = flaky_from
                else:
                    # Retried and still down — keep the transient context.
                    cell["first_attempt"] = flaky_from
            if balance_too_low(cell.get("summary") or ""):
                raise BalanceTooLow(f"{alias}/{spec['id']}: {cell.get('summary')}")
            cell["model"] = market.family(alias)
            cells.append(cell)
            # Fail-fast: a failed or errored check means the route is broken —
            # the remaining specs of this route are skipped, not run.
            if cell.get("status") in ("fail", "error"):
                for remaining in registry[i + 1 :]:
                    skip = result(
                        check_id=remaining["id"],
                        alias=alias,
                        status="skipped",
                        summary="not run — earlier check failed",
                    )
                    skip["model"] = market.family(alias)
                    cells.append(skip)
                break
    return cells, errors


def candidate_routes(groups: list[dict]) -> list[tuple[str, str]]:
    """(route, model) pairs in config order, deduplicated by route."""
    seen: set[str] = set()
    pairs: list[tuple[str, str]] = []
    for group in groups:
        for route in group["routes"]:
            if route not in seen:
                seen.add(route)
                pairs.append((route, group["model"]))
    return pairs


def run_candidate_sweep(
    client: InferHubClient, pairs: list[tuple[str, str]], registry: list[dict]
) -> tuple[list[dict], list[str]]:
    """Probe candidate routes; every cell tagged candidate:true + owning model."""
    cells: list[dict] = []
    errors: list[str] = []
    for route, model in pairs:
        route_cells, route_errors = collect_cells(client, [route], registry)
        for cell in route_cells:
            cell["candidate"] = True
            cell["model"] = model
        cells.extend(route_cells)
        errors.extend(route_errors)
    return cells, errors


def main() -> int:
    key = os.environ.get("INFERHUB_API_KEY", "").strip()
    if not key:
        print("INFERHUB_API_KEY is required", file=sys.stderr)
        return 2
    client = InferHubClient(key)
    aliases = load_aliases()
    registry = load_registry()
    # Market radar: the catalog picks the candidates. A catalog hiccup must
    # not kill the run — degrade to board-only and say so in runner_errors.
    market_error: str | None = None
    try:
        groups = market.shortlist(key, market.load_pricing(root=repo_root()), root=repo_root())
    except Exception as exc:  # noqa: BLE001
        groups = []
        market_error = f"market shortlist failed: {exc}"
    cand_pairs = candidate_routes(groups)
    started = datetime.now(timezone.utc)
    try:
        cells, errors = collect_cells(client, aliases, registry)
    except BalanceTooLow as exc:
        print(f"Aborting probe, no run written — {exc}", file=sys.stderr)
        return 3
    if market_error:
        errors.append(market_error)
    if cand_pairs:
        try:
            cand_cells, cand_errors = run_candidate_sweep(client, cand_pairs, registry)
            cells.extend(cand_cells)
            errors.extend(cand_errors)
        except BalanceTooLow as exc:
            # board data is already safe; stop the sweep, keep the run
            errors.append(f"candidate sweep aborted early: {exc}")
    finished = datetime.now(timezone.utc)
    run_payload = {
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "origin": "github-actions",
        "api": URL,
        "aliases": aliases,
        "candidates": [route for route, _ in cand_pairs],
        "checks": [c["id"] for c in registry],
        "cells": cells,
        "runner_errors": errors,
    }
    try:
        market.record_proven(run_payload, root=repo_root())
    except Exception as exc:  # noqa: BLE001 — proven cache must never break a run
        run_payload["runner_errors"].append(f"proven cache write failed: {exc}")
    try:
        from probe.costs import attribute_costs, fetch_log_rows

        rows = fetch_log_rows(key, range_="24h", after=started - timedelta(minutes=5))
        costs = attribute_costs(run_payload, rows)
        run_payload["cost"] = costs
    except Exception as exc:  # noqa: BLE001 — cost reporting must never break a run
        run_payload["cost"] = None
        run_payload["runner_errors"].append(f"cost attribution failed: {exc}")
    stamp = started.strftime("%Y-%m-%dT%H%M%SZ")
    payload = run_payload
    out = repo_root() / "data" / "runs" / f"{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
