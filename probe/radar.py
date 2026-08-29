"""Radar verdict — is the in-use route still the best price?

Reads the newest committed run plus data/pricing.json (zero API calls),
and per model family compares the cheapest PASSING candidate — both core
and cache green — against the billed $/M of the cheapest in-use route.
A margin of MARGIN_ALERT_PCT or more that is new or wider than the last
alert prints one ALERT line; alert state lives in .radar-ledger.json
(repo root, gitignored). Advisory only: main() always exits 0.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from probe import market
from probe.registry import load_aliases, repo_root

MARGIN_ALERT_PCT = 15.0
LEDGER_NAME = ".radar-ledger.json"
NOTIFY_SESSION_ENV = "INFERHUB_RADAR_SESSION"
NOTIFY_PROFILE_ENV = "INFERHUB_RADAR_PROFILE"


def latest_run(root: Path | None = None) -> dict | None:
    """Newest data/runs/*.json payload, or None."""
    root = root or repo_root()
    files = sorted((root / "data" / "runs").glob("*.json"))
    if not files:
        return None
    try:
        return json.loads(files[-1].read_text())
    except (OSError, ValueError):
        return None


def load_ledger(root: Path | None = None) -> dict:
    root = root or repo_root()
    try:
        data = json.loads((root / LEDGER_NAME).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_ledger(ledger: dict, root: Path | None = None) -> Path:
    root = root or repo_root()
    path = root / LEDGER_NAME
    path.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n")
    return path


def candidate_cache_pct(run: dict, route: str) -> float | None:
    """Measured cache share (0-100) from the route's cache cell, if any."""
    for cell in run.get("cells") or []:
        if not cell.get("candidate") or cell.get("alias") != route:
            continue
        if cell.get("check_id") != "cache":
            continue
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
    return None


def family_verdicts(run: dict, pricing_routes: dict, aliases: list[str]) -> list[dict]:
    """Per board family with a billed incumbent: the best passing challenger.

    challenger_usd_m uses the candidate's billed asks from pricing.json,
    its measured probe cache when the cache cell carries evidence, and the
    family token mix of the incumbent. A challenger exists only when it
    undercuts the incumbent bar. Families are ISOLATED: one family's
    malformed pricing data is skipped with a stderr note and never kills
    the other families' verdicts (or the site build).
    """
    checks = list(run.get("checks") or [])
    statuses: dict[str, dict] = {}
    for cell in run.get("cells") or []:
        if cell.get("candidate") and cell.get("alias"):
            statuses.setdefault(cell["alias"], {})[cell.get("check_id")] = cell.get("status")
    fams: dict[str, list[str]] = {}
    for alias in aliases:
        fams.setdefault(market.family(alias), []).append(alias)
    verdicts = []
    for fam in sorted(fams):
        try:
            ctx = market.family_context(pricing_routes, fams[fam])
            verdict = _family_verdict(fam, ctx[fam], run, checks, statuses, pricing_routes)
        except Exception as exc:  # noqa: BLE001 — one bad family must not kill the rest
            print(f"radar: family {fam} skipped: {exc}", file=sys.stderr)
            continue
        if verdict is not None:
            verdicts.append(verdict)
    return verdicts


def _family_verdict(fam: str, info: dict, run: dict, checks: list[str],
                    statuses: dict[str, dict], pricing_routes: dict) -> dict | None:
    """One family's verdict; None when the incumbent has nothing billed."""
    inc_alias: str | None = None
    inc_entry: dict = {}
    bar: float | None = None
    for alias in info["incumbents"]:
        entry = pricing_routes.get(alias) or {}
        eff = entry.get("eff_per_mtok")
        if eff is None:
            continue
        if bar is None or eff < bar:
            bar, inc_alias, inc_entry = eff, alias, entry
    if bar is None:
        return None  # nothing billed for the incumbent — no bar to beat
    best: tuple[str, float, float, str] | None = None
    for route, route_statuses in statuses.items():
        if market.family(route) != fam:
            continue
        if not checks or not all(route_statuses.get(cid) == "pass" for cid in checks):
            continue
        entry = pricing_routes.get(route) or {}
        ask_in, ask_out = entry.get("ask_in"), entry.get("ask_out")
        if ask_in is None or ask_out is None:
            continue  # no billed asks yet — cannot price the challenger
        measured = candidate_cache_pct(run, route)
        if measured is not None:
            rate, cache_src = measured / 100.0, "probe"
        else:
            rate, cache_src = info["cache_rate"], "family"
        usd_m = market.predicted_usd_m(ask_in, ask_out, rate, info["w_in"], info["w_out"])
        if usd_m < bar and (best is None or usd_m < best[1]):
            cache_pct = measured if measured is not None else info["cache_rate"] * 100.0
            best = (route, usd_m, cache_pct, cache_src)
    verdict = {
        "family": fam,
        "incumbent": inc_alias,
        "incumbent_usd_m": bar,
        "incumbent_reqs": inc_entry.get("reqs") or 0,
        "challenger": None,
        "challenger_usd_m": None,
        "challenger_cache_pct": None,
        "challenger_cache_source": None,
        "margin_pct": None,
    }
    if best:
        route, usd_m, cache_pct, cache_src = best
        verdict["challenger"] = route
        verdict["challenger_usd_m"] = usd_m
        verdict["challenger_cache_pct"] = cache_pct
        verdict["challenger_cache_source"] = cache_src
        verdict["margin_pct"] = (bar - usd_m) / bar * 100.0
    return verdict


def due_alerts(verdicts: list[dict], ledger: dict) -> list[dict]:
    """Verdicts whose margin crossed the threshold newly or widened."""
    due = []
    for verdict in verdicts:
        margin = verdict["margin_pct"]
        if margin is None or margin < MARGIN_ALERT_PCT:
            continue
        try:
            last = float(ledger.get(verdict["challenger"]))
        except (TypeError, ValueError):
            last = None
        if last is None or margin > last:
            due.append(verdict)
    return due


def notify_command(due: list[dict]) -> list[str] | None:
    """`opencrabs session notify` argv for the due alerts, or None.

    None when no session is configured (INFERHUB_RADAR_SESSION unset) or
    nothing is due — CI stays silent, local manual runs opt in.
    """
    session = os.environ.get(NOTIFY_SESSION_ENV, "").strip()
    if not session or not due:
        return None
    lines = "\n".join(
        f"{v['family']}: {v['challenger']} ${v['challenger_usd_m']:.4f}/M vs "
        f"in-use {v['incumbent']} ${v['incumbent_usd_m']:.4f}/M "
        f"({v['margin_pct']:.0f}% cheaper)"
        for v in due
    )
    profile = os.environ.get(NOTIFY_PROFILE_ENV, "").strip()
    cmd = ["opencrabs"]
    if profile:
        cmd += ["-p", profile]
    cmd += [
        "session",
        "notify",
        "--title",
        "inferhub radar",
        "--sender",
        "inferhub-watch",
        "--text",
        f"Cheaper passing challenger on the board:\n{lines}",
    ]
    cmd.append(session)
    return cmd


def notify_alerts(due: list[dict]) -> None:
    """Best-effort delivery of due alerts; never raises (advisory job)."""
    cmd = notify_command(due)
    if cmd is None:
        return
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            print(f"radar: notify failed ({proc.returncode}): {proc.stderr.strip()}")
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"radar: notify error: {exc}")


def main(root: Path | None = None) -> int:
    root = root or repo_root()
    try:
        run = latest_run(root)
        if run is None:
            print("radar: no run data yet — no alert")
            return 0
        pricing = market.load_pricing(root)
        verdicts = family_verdicts(run, (pricing or {}).get("routes") or {}, load_aliases())
        for v in verdicts:
            head = f"verdict {v['family']}: in use {v['incumbent']} ${v['incumbent_usd_m']:.4f}/M"
            if v["challenger"]:
                print(
                    f"{head} · best passing {v['challenger']} "
                    f"${v['challenger_usd_m']:.4f}/M (−{v['margin_pct']:.0f}%)"
                )
            else:
                print(f"{head} · no passing challenger")
        ledger = load_ledger(root)
        due = due_alerts(verdicts, ledger)
        for v in due:
            print(
                f"ALERT {v['family']}: {v['challenger']} bills ${v['challenger_usd_m']:.4f}/M "
                f"vs in-use {v['incumbent']} ${v['incumbent_usd_m']:.4f}/M — "
                f"{v['margin_pct']:.0f}% cheaper"
            )
            ledger[v["challenger"]] = round(v["margin_pct"], 2)
        if due:
            save_ledger(ledger, root)
        else:
            print("no alert")
        notify_alerts(due)
    except Exception as exc:  # noqa: BLE001 — advisory job; never break the cron
        print(f"radar error: {exc} — no alert")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
