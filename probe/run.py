from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from probe.http import InferHubClient
from probe.payloads import URL
from probe.registry import load_aliases, load_check_module, load_registry, repo_root
from probe.result import result

BALANCE_MARKERS = ("balance too low", "insufficient_balance")


class BalanceTooLow(RuntimeError):
    """InferHub reported an out-of-balance error; the probe must abort."""


def balance_too_low(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in BALANCE_MARKERS)


def collect_cells(
    client: InferHubClient, aliases: list[str], registry: list[dict]
) -> tuple[list[dict], list[str]]:
    cells = []
    errors = []
    for alias in aliases:
        for spec in registry:
            module = load_check_module(spec["id"])
            try:
                cell = module.run(client, alias)
            except Exception as exc:  # noqa: BLE001 — keep the day, record the cell
                if balance_too_low(str(exc)):
                    raise BalanceTooLow(f"{alias}/{spec['id']}: {exc}") from exc
                errors.append(f"{alias}/{spec['id']}: {exc}")
                cells.append(
                    result(
                        check_id=spec["id"],
                        alias=alias,
                        status="error",
                        summary=str(exc),
                    )
                )
                continue
            if balance_too_low(cell.get("summary") or ""):
                raise BalanceTooLow(f"{alias}/{spec['id']}: {cell.get('summary')}")
            cells.append(cell)
    return cells, errors


def main() -> int:
    key = os.environ.get("INFERHUB_API_KEY", "").strip()
    if not key:
        print("INFERHUB_API_KEY is required", file=sys.stderr)
        return 2
    client = InferHubClient(key)
    aliases = load_aliases()
    registry = load_registry()
    started = datetime.now(timezone.utc)
    try:
        cells, errors = collect_cells(client, aliases, registry)
    except BalanceTooLow as exc:
        print(f"Aborting probe, no run written — {exc}", file=sys.stderr)
        return 3
    finished = datetime.now(timezone.utc)
    run_payload = {
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "origin": "github-actions",
        "api": URL,
        "aliases": aliases,
        "checks": [c["id"] for c in registry],
        "cells": cells,
        "runner_errors": errors,
    }
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
