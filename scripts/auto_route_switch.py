"""Automated route switching pipeline for Inferhub Watch (Issue #12, #22).

Evaluates candidate routes against the current model using Artificial Analysis IQ,
gated projected effective price (matching the board crown, probe/basis.py), and
empirical throughput (TPS) weighting. Automatically switches the active route when a qualified
alternative offers >15% higher Value:

    Value = (IQ / eff_price) * (tps / 50.0) ** 0.50

Criteria:
- AA IQ >= 35.0 (hard floor)
- Supports tool calling (`supports_tools` is True)
- DeepSeek-v4 guard: must have a date tag (e.g., -0731, -0813), while DeepSeek-v4.1+ is allowed
- Switch threshold: Candidate Value > Current Value * 1.15
- OpenCrabs updates: config.toml ([agent].default_model, [agent].subagent_model,
  [providers.custom.inferhub].default_model, and append to [providers.custom.inferhub].models)
  and active unarchived sessions in opencrabs.db
- Switch alert via `opencrabs session notify` (Issue #14): the notified session owns the
  Telegram card; this script never touches Telegram or the Bot API directly.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomllib

# Add project root to sys.path
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from probe import basis, official_compare, pgstore, pricing, tps_qual
from probe.http import InferHubClient
from scripts.sync_usage_logs import resolve_slug

logger = logging.getLogger("auto_route_switch")

DEFAULT_CONFIG_PATH = Path("/root/.opencrabs/profiles/ops/config.toml")
DEFAULT_KEYS_PATH = Path("/root/.opencrabs/profiles/ops/keys.toml")
DEFAULT_DB_PATH = Path("/root/.opencrabs/profiles/ops/opencrabs.db")
DEFAULT_ROOT_DB_PATH = Path("/root/.opencrabs/opencrabs.db")
IQ_FLOOR = 35.0
SWITCH_THRESHOLD = 0.15  # >15% higher value
DEFAULT_TPS_REF = 50.0  # Fallback reference baseline TPS when fleet prior is unavailable
TPS_REF = DEFAULT_TPS_REF  # Backwards compatibility alias
TPS_WEIGHT_EXPONENT = 0.50  # Square-root power-law exponent for TPS weight

# --- Issue #27: basis consistency, dwell, floor-point pinning ---------------
# The thrash incident: the incumbent was ranked on its realized (billed)
# basis while the challenger was ranked on its projected floor basis, and
# the 58x gap between those two bases read as a 561% "gain". Every
# evaluation now computes ONE basis for every candidate.
BASIS_FLOOR = "floor"
BASIS_REALIZED = "realized"
BASIS_MIN_SAMPLES = 5
"""Streaming samples a candidate needs in the rolling 24h window before the
realized basis is admissible at all. Below this the whole evaluation drops
to the projected floor basis."""
DWELL_SECONDS = 6 * 3600
"""Minimum dwell after a real switch before another flip is eligible."""
DWELL_OVERRIDE_GAIN = 3.0
"""Gain on the CONSISTENT basis that overrides the dwell gate (>300%)."""
SWITCHER_STATE_NAME = "switcher_state.json"


# Inferhub Watch Telegram group whose bound session receives switch alerts (Issue #14).
INFERHUB_WATCH_CHAT_ID = "-1004379632866"
# HQ forum topic (thread) inside that group — the switch alert is owner-facing, so it
# must land in HQ topic 2, never in whichever worker topic most recently saw traffic
# (Issue #21, Finding D). `session_bindings` is keyed on session_id with no
# (chat_id, thread_id) uniqueness, so a chat_id-only lookup lets a worker topic steal
# the alert.
INFERHUB_WATCH_HQ_THREAD_ID = 2
NOTIFY_PROFILE = "ops"
NOTIFY_TITLE = "Inferhub Route Auto-Switched"
# Receipt verdicts accepted from `opencrabs session notify --confirm` (Issue #14).
# The CLI's text output renders the confirm verdict as prose in the detail
# line (daemon confirm_route): "Confirmed end-to-end" = woke, "Confirmed
# queued" = queued_pending_drain, "no wake was observed" = delivered. The
# state names themselves are never printed, so matching is on the prose.
# NOTE: exit code 0 alone is NOT enough — parked (channel not claimed since
# boot, #1206) also exits 0 but carries no confirm verdict.
CONFIRM_VERDICTS = (
    ("woke", "Confirmed end-to-end"),
    ("queued_pending_drain", "Confirmed queued"),
    ("delivered", "no wake was observed"),
)


# Models or publishers known not to support standard chat/tool calling
NON_TOOL_PATTERNS = (
    "embed", "rerank", "tts", "stt", "whisper", "flux", "sora", "ideogram", "recraft", "midjourney"
)


@dataclass
class RouteCandidate:
    route: str
    iq: float
    ask_in: float
    ask_out: float
    eff_price: float
    tps: float
    value: float
    supports_tools: bool
    supports_cache: bool
    tps_source: str = "fallback"  # "prod", "qual", "fallback"
    # Issue #27: both bases are carried, but only ONE is ever ranked on.
    floor_price: float = 0.0  # projected/catalog floor basis
    realized_price: float | None = None  # billed basis (None = no billing)
    realized_samples: int = 0  # streaming samples behind realized_price


def calculate_value(
    iq: float,
    eff_price: float | None = None,
    ask_in: float | None = None,
    ask_out: float | None = None,
    tps: float | None = None,
    tps_ref: float = TPS_REF,
    weight_exp: float = TPS_WEIGHT_EXPONENT,
) -> float:
    """Calculate empirical value metric:
    Value = (IQ / eff_price) * (tps / tps_ref) ** weight_exp

    Backwards compatibility: If eff_price is not provided, computes raw ask mix:
    denom = 0.99 * ask_in + 0.01 * ask_out.
    """
    if eff_price is None:
        if ask_in is None or ask_out is None:
            return 0.0
        denom = (0.99 * ask_in) + (0.01 * ask_out)
        if denom <= 0:
            return 0.0
        eff_price = denom

    if eff_price <= 0 or iq is None or iq <= 0:
        return 0.0

    base_val = iq / eff_price

    if tps is None or tps <= 0:
        tps = tps_ref

    tps_multiplier = (tps / tps_ref) ** weight_exp
    return base_val * tps_multiplier


def _catalog_floor_price(route: str, info: dict[str, Any], pricing_payload: dict[str, Any] | None) -> float:
    """Cold-start floor: catalog asks discounted by the fleet hit prior."""
    try:
        ask_in = float(info.get("ask_in") or 0.0)
        ask_out = float(info.get("ask_out") or 0.0)
    except (ValueError, TypeError):
        return 0.0

    if ask_in <= 0.0 or ask_out <= 0.0:
        return 0.0

    # If cache supported, apply fleet prior hit rate (official_compare.fleet_hit_prior)
    supports_cache = bool(info.get("supports_cache", False))
    if supports_cache:
        # 50% discount on cached input tokens per InferHub standard
        routes_dict = (pricing_payload or {}).get("routes", {})
        prior = official_compare.fleet_hit_prior(routes_dict) if routes_dict else None
        hit_factor = prior if prior is not None else 0.0
        eff_in = ask_in * (1.0 - 0.5 * hit_factor)
    else:
        eff_in = ask_in

    return (0.99 * eff_in) + (0.01 * ask_out)

def resolve_floor_price(
    route: str,
    info: dict[str, Any],
    pricing_payload: dict[str, Any] | None = None,
    dated_snapshots: list | None = None,
    use_proj: bool = True,
) -> float:
    """The PROJECTED/FLOOR basis price for a route (issue #27).

    Forward-looking and traffic-independent: the smoothed projection once
    the crown gate passes, else the catalog floor ask with the fleet hit
    prior. It never reads billed history, so it does not move when a
    route's traffic moves — which is exactly why it is safe to rank
    every candidate on.
    """
    if pricing_payload is not None:
        dated = dated_snapshots if dated_snapshots is not None else []
        if use_proj:
            proj = basis.projected(pricing_payload, route, dated)
            if proj is not None and proj > 0:
                return float(proj)

    return _catalog_floor_price(route, info, pricing_payload)

def resolve_realized_price(
    route: str,
    pricing_payload: dict[str, Any] | None = None,
) -> tuple[float | None, int]:
    """The REALIZED (billed) basis price and the streaming samples behind it.

    Returns ``(price, samples)``. ``price`` is None when the route has no
    billed history; ``samples`` counts the streaming samples in the same
    rolling 24h perf window, which is what gates the realized basis.

    The frozen-median trap (#27): a route that has just lost its traffic
    stops accumulating billed rows, so its median billed ask freezes at
    whatever it was when traffic left. That is why the realized basis is
    only admissible when EVERY candidate can supply it.
    """
    if pricing_payload is None:
        return None, 0
    eff = basis.realized(pricing_payload, route)
    perf_models = ((pricing_payload.get("perf") or {}).get("models") or {})
    entry = perf_models.get(route) or {}
    samples = int(entry.get("tps_samples") or 0)
    if eff is None:
        return None, samples
    try:
        eff_f = float(eff)
    except (TypeError, ValueError):
        return None, samples
    if eff_f <= 0:
        return None, samples
    return eff_f, samples

def select_basis(
    candidates: list[RouteCandidate],
    min_samples: int = BASIS_MIN_SAMPLES,
) -> str:
    """One basis for the whole evaluation (issue #27 item 1).

    Realized is admissible only when EVERY candidate carries at least
    ``min_samples`` streaming samples in the same 24h window. Any
    candidate short of that — a cold route, or one whose traffic just
    stopped — drops the whole evaluation to the floor basis, because a
    MIXED evaluation is what produced the thrash: incumbent on frozen
    realized, challenger on floor, and the gap between the two bases read
    as a real value delta.
    """
    if not candidates:
        return BASIS_FLOOR
    for cand in candidates:
        if cand.realized_price is None or cand.realized_samples < min_samples:
            return BASIS_FLOOR
    return BASIS_REALIZED

def apply_basis(
    candidates: list[RouteCandidate],
    mode: str,
    tps_ref: float | None = None,
) -> None:
    """Set every candidate's ranked price and value on ONE basis in place."""
    if tps_ref is None:
        tps_ref = TPS_REF
    for cand in candidates:
        if mode == BASIS_REALIZED and cand.realized_price is not None:
            cand.eff_price = cand.realized_price
        else:
            cand.eff_price = cand.floor_price
        cand.value = (
            calculate_value(cand.iq, eff_price=cand.eff_price, tps=cand.tps, tps_ref=tps_ref)
            if (cand.iq > 0 and cand.eff_price > 0)
            else 0.0
        )

def resolve_effective_price(
    route: str,
    info: dict[str, Any],
    pricing_payload: dict[str, Any] | None = None,
    dated_snapshots: list | None = None,
    use_proj: bool = True,
) -> float:
    """Deprecated single-price view (pre-#27 shape).

    Retained for callers that only want "a price" — it is the floor basis,
    never a realized/floor mix. Evaluation code must use
    ``resolve_floor_price`` + ``resolve_realized_price`` + ``select_basis``.
    """
    return resolve_floor_price(
        route,
        info,
        pricing_payload=pricing_payload,
        dated_snapshots=dated_snapshots,
        use_proj=use_proj,
    )


def resolve_fleet_tps_ref(pricing_payload: dict[str, Any] | None = None) -> float:
    """Resolve reference baseline TPS from live rolling fleet production median."""
    prior = official_compare.fleet_tps_prior(pricing_payload)
    if prior is not None and 1.0 <= prior <= 500.0:
        return float(prior)
    return DEFAULT_TPS_REF


def resolve_route_tps(
    route: str,
    pricing_payload: dict[str, Any] | None = None,
    qual_runs: dict[str, Any] | None = None,
    default_tps: float | None = None,
) -> tuple[float, str]:
    """Resolve route TPS hierarchically:
    1. 24h rolling production perf_stats from pricing_payload (n >= 5) -> ("prod")
    2. Qualified sustained TPS probe from qual_runs (1.0 <= tps <= 500.0) -> ("qual")
    3. Fleet empirical prior (or DEFAULT_TPS_REF fallback) -> ("fallback")

    Returns:
        (tps_value, source_name)
    """
    if default_tps is None:
        default_tps = resolve_fleet_tps_ref(pricing_payload)

    # Tier 1: Production 24h perf stats
    if pricing_payload is not None:
        perf = pricing_payload.get("perf") or {}
        models_perf = perf.get("models") or {}
        model_stats = models_perf.get(route) or {}
        tps_mean = model_stats.get("tps_mean")
        n_samples = model_stats.get("tps_samples") or model_stats.get("reqs") or model_stats.get("n", 0)
        if tps_mean is not None and n_samples >= 5 and 1.0 <= float(tps_mean) <= 500.0:
            return float(tps_mean), "prod"

    # Tier 2: Candidate qualification probe runs
    if qual_runs is not None:
        qual_models = qual_runs.get("models") or {}
        qual_stat = qual_models.get(route) or {}
        q_tps = qual_stat.get("tps")
        if q_tps is not None and 1.0 <= float(q_tps) <= 500.0:
            return float(q_tps), "qual"

    # Tier 3: Fleet empirical prior default
    return default_tps, "fallback"


def resolve_inferhub_key(
    config_path: Path | None = None,
    keys_path: Path | None = None,
) -> str:
    """Resolve InferHub API key from env or keys.toml."""
    env_key = os.environ.get("INFERHUB_API_KEY", "").strip()
    if env_key:
        return env_key

    kp = keys_path
    if kp is None and config_path is not None:
        cand = config_path.with_name("keys.toml")
        if cand.exists():
            kp = cand
    if kp is None and config_path is not None and DEFAULT_CONFIG_PATH.exists() and config_path == DEFAULT_CONFIG_PATH:
        if DEFAULT_KEYS_PATH.exists():
            kp = DEFAULT_KEYS_PATH

    if kp and kp.exists():
        try:
            with kp.open("rb") as f:
                data = tomllib.load(f)
            return (
                data.get("providers", {})
                .get("custom", {})
                .get("inferhub", {})
                .get("api_key", "")
                .strip()
            )
        except Exception as exc:
            logger.warning(f"Failed to read keys from {kp}: {exc}")
    return ""


def is_qualified(
    route: str,
    model_info: dict[str, Any],
    iq: float | None,
    floor_iq: float = IQ_FLOOR,
) -> bool:
    """Check qualification rules:
    1. iq >= floor_iq (35.0)
    2. Tool calling / chat model supported (explicit False or non-tool names excluded)
    3. DeepSeek-v4 guard: if model name is deepseek-v4 (not v4.1+), it must have a date tag like -0731 or -0813.
    """
    if iq is None or iq < floor_iq:
        return False

    # Check explicit supports_tools field if present
    if "supports_tools" in model_info and model_info["supports_tools"] is False:
        return False

    # Check for non-tool task types in route name
    route_lower = route.lower()
    if any(pat in route_lower for pat in NON_TOOL_PATTERNS):
        return False

    # Check pricing validity
    try:
        ask_in = float(model_info.get("ask_in") or 0.0)
        ask_out = float(model_info.get("ask_out") or 0.0)
    except (ValueError, TypeError):
        return False

    if ask_in <= 0.0 or ask_out <= 0.0:
        return False

    # DeepSeek-v4 guard
    # Matches deepseek-v4 but NOT deepseek-v4.1, deepseek-v4.2, deepseek-v4-1, etc.
    # If it is deepseek-v4 without a subversion > 4.0, it must contain a date tag (-[0-9]{4}).
    tail = route.rsplit("/", 1)[-1].lower()
    if "deepseek-v4" in tail:
        # Check if it has a minor version like deepseek-v4.1 or deepseek-v4-1
        has_minor_version = bool(re.search(r"deepseek-v4[._-][1-9]", tail))
        if not has_minor_version:
            # Must have date tag e.g. -0731, -0813, -20260731
            has_date_tag = bool(re.search(r"-\d{4,8}", tail))
            if not has_date_tag:
                return False

    return True


def evaluate_candidates(
    catalog_models: dict[str, Any],
    intel_slugs: dict[str, Any],
    aa_map: dict[str, Any],
    floor_iq: float = IQ_FLOOR,
    pricing_payload: dict[str, Any] | None = None,
    dated_snapshots: list | None = None,
    qual_runs: dict[str, Any] | None = None,
    use_proj: bool = True,
    tps_ref: float | None = None,
) -> dict[str, RouteCandidate]:
    """Evaluate and build RouteCandidate objects for all catalog models."""
    if tps_ref is None:
        tps_ref = resolve_fleet_tps_ref(pricing_payload)

    candidates: dict[str, RouteCandidate] = {}
    for route, info in catalog_models.items():
        slug = resolve_slug(route, aa_map, intel_slugs)
        iq = (intel_slugs.get(slug) or {}).get("iq") if slug else None
        if iq is not None:
            iq = float(iq)

        supports_tools = bool(info.get("supports_tools", True))
        supports_cache = bool(info.get("supports_cache", False))
        try:
            ask_in = float(info.get("ask_in") or 0.0)
            ask_out = float(info.get("ask_out") or 0.0)
        except (ValueError, TypeError):
            ask_in, ask_out = 0.0, 0.0

        floor_price = resolve_floor_price(
            route,
            info,
            pricing_payload=pricing_payload,
            dated_snapshots=dated_snapshots,
            use_proj=use_proj,
        )
        realized_price, realized_samples = resolve_realized_price(
            route, pricing_payload=pricing_payload
        )
        tps, tps_source = resolve_route_tps(
            route, pricing_payload=pricing_payload, qual_runs=qual_runs, default_tps=tps_ref
        )
        # Provisional ranking is on the floor basis; find_best_route re-applies
        # whichever basis the WHOLE evaluation selects (issue #27 item 1).
        val = calculate_value(iq or 0.0, eff_price=floor_price, tps=tps, tps_ref=tps_ref) if (iq is not None and floor_price > 0) else 0.0

        candidates[route] = RouteCandidate(
            route=route,
            iq=iq or 0.0,
            ask_in=ask_in,
            ask_out=ask_out,
            eff_price=floor_price,
            tps=tps,
            value=val,
            supports_tools=supports_tools,
            supports_cache=supports_cache,
            tps_source=tps_source,
            floor_price=floor_price,
            realized_price=realized_price,
            realized_samples=realized_samples,
        )
    return candidates


def find_best_route(
    catalog_models: dict[str, Any],
    intel_slugs: dict[str, Any],
    aa_map: dict[str, Any],
    current_model: str,
    floor_iq: float = IQ_FLOOR,
    threshold: float = SWITCH_THRESHOLD,
    pricing_payload: dict[str, Any] | None = None,
    dated_snapshots: list | None = None,
    qual_runs: dict[str, Any] | None = None,
    use_proj: bool = True,
    client: Any | None = None,
    qual_path: Path | None = None,
    tps_ref: float | None = None,
    out: dict[str, Any] | None = None,
) -> tuple[bool, RouteCandidate | None, RouteCandidate | None, str]:
    """Determine if a route switch is needed.

    Returns:
        (should_switch, best_candidate, current_candidate, reason)

    ``out``, when given, is filled with diagnostics — notably
    ``basis_mode`` (issue #27 item 1), the ONE basis every candidate in
    this evaluation was ranked on.
    """
    if tps_ref is None:
        tps_ref = resolve_fleet_tps_ref(pricing_payload)

    candidates = evaluate_candidates(
        catalog_models,
        intel_slugs,
        aa_map,
        floor_iq=floor_iq,
        pricing_payload=pricing_payload,
        dated_snapshots=dated_snapshots,
        qual_runs=qual_runs,
        use_proj=use_proj,
        tps_ref=tps_ref,
    )
    current_cand = candidates.get(current_model)

    qualified_candidates = [
        c for r, c in candidates.items()
        if is_qualified(r, catalog_models.get(r, {}), c.iq if c.iq > 0 else None, floor_iq=floor_iq)
    ]

    if not qualified_candidates:
        if out is not None:
            out["basis_mode"] = BASIS_FLOOR
        return False, None, current_cand, "No qualified candidates found meeting IQ and tool requirements."

    # --- Issue #27 item 1: ONE basis for the whole evaluation ----------------
    # The comparison set is every qualified challenger PLUS the incumbent, so
    # the incumbent can never be ranked on a basis that no challenger supplies
    # (that mix is what made a 58x basis gap read as a 561% gain).
    basis_pool = list(qualified_candidates)
    if current_cand is not None and all(c is not current_cand for c in basis_pool):
        basis_pool.append(current_cand)
    basis_mode = select_basis(basis_pool)
    apply_basis(list(candidates.values()), basis_mode, tps_ref=tps_ref)
    if out is not None:
        out["basis_mode"] = basis_mode
        out["basis_samples"] = min(
            (c.realized_samples for c in basis_pool), default=0
        )

    # Sort descending by value
    qualified_candidates.sort(key=lambda c: c.value, reverse=True)

    # Ensure the top candidate has verified empirical TPS before crowning or switching.
    # An unverified candidate (fallback 50.0) is live-probed via sustained qualification probe.
    if client is not None:
        while qualified_candidates:
            top = qualified_candidates[0]
            if top.tps_source != "fallback":
                break  # Verified via prod stats or existing qualification probe

            logger.info(f"Top candidate {top.route} has unverified TPS (fallback {top.tps:.1f}); probing sustained TPS...")
            res = tps_qual.run_candidate_tps_qual(client, top.route, qual_path=qual_path)
            if res.get("valid") and res.get("tps") is not None:
                new_tps = float(res["tps"])
                logger.info(f"Candidate {top.route} qualified: measured TPS={new_tps:.1f}")
                top.tps = new_tps
                top.tps_source = "qual"
                top.value = calculate_value(top.iq, eff_price=top.eff_price, tps=top.tps, tps_ref=tps_ref)
                qualified_candidates.sort(key=lambda c: c.value, reverse=True)
            else:
                logger.warning(
                    f"Candidate {top.route} failed sustained TPS qualification ({res.get('error', 'invalid metrics')}); "
                    "disqualifying from switch contention."
                )
                qualified_candidates.pop(0)

    if not qualified_candidates:
        return False, None, current_cand, "No qualified candidates meeting IQ, tool, and TPS requirements."

    best = qualified_candidates[0]

    if current_cand is None or current_cand.value <= 0:
        return True, best, current_cand, f"Current model {current_model} not evaluated or has 0 value; selecting top candidate {best.route} (basis={basis_mode})."

    gain = (best.value - current_cand.value) / current_cand.value
    if best.route != current_model and gain > threshold:
        return (
            True,
            best,
            current_cand,
            f"Candidate {best.route} (Val={best.value:.1f}, TPS={best.tps:.1f}[{best.tps_source}]) exceeds {current_model} (Val={current_cand.value:.1f}, TPS={current_cand.tps:.1f}[{current_cand.tps_source}]) by {gain * 100:.1f}% (threshold {threshold * 100:.0f}%, basis={basis_mode}).",
        )

    return (
        False,
        best,
        current_cand,
        f"Top candidate {best.route} (Val={best.value:.1f}, TPS={best.tps:.1f}[{best.tps_source}]) gain over {current_model} is {gain * 100:.1f}% (<= {threshold * 100:.0f}% threshold, basis={basis_mode}).",
    )


def update_opencrabs_config(config_path: Path, new_model: str) -> bool:
    """Update OpenCrabs config.toml:
    - [agent].default_model = new_model
    - [agent].subagent_model = new_model
    - [providers.custom.inferhub].default_model = new_model
    - append new_model to [providers.custom.inferhub].models if not present
    """
    if not config_path.exists():
        logger.warning(f"Config path {config_path} does not exist")
        return False

    content = config_path.read_text(encoding="utf-8")
    lines = content.splitlines()
    new_lines = []
    current_section = ""

    # Parse and modify line by line to preserve comments and formatting
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            current_section = stripped[1:-1].strip()
            new_lines.append(line)
            continue

        if current_section == "agent":
            if re.match(r"^default_model\s*=", stripped):
                new_lines.append(f'default_model = "{new_model}"')
                continue
            if re.match(r"^subagent_model\s*=", stripped):
                new_lines.append(f'subagent_model = "{new_model}"')
                continue

        if current_section == "providers.custom.inferhub":
            if re.match(r"^default_model\s*=", stripped):
                new_lines.append(f'default_model = "{new_model}"')
                continue
            if re.match(r"^models\s*=\s*\[", stripped):
                # Ensure new_model is in the list
                m = re.search(r"\[(.*)\]", stripped)
                if m:
                    raw_items = m.group(1).split(",")
                    items = [it.strip().strip('"').strip("'") for it in raw_items if it.strip()]
                    if new_model not in items:
                        items.append(new_model)
                    formatted_items = ", ".join(f'"{it}"' for it in items)
                    new_lines.append(f"models = [{formatted_items}]")
                    continue

        new_lines.append(line)

    updated_content = "\n".join(new_lines) + "\n"
    config_path.write_text(updated_content, encoding="utf-8")
    return True


def update_active_sessions(db_path: Path, new_model: str, target_provider: str = "custom:inferhub") -> int:
    """Update active unarchived sessions in opencrabs.db to point to new_model."""
    if not db_path.exists():
        logger.warning(f"Database path {db_path} does not exist")
        return 0

    conn = sqlite3.connect(str(db_path))
    try:
        with conn:
            cur = conn.cursor()
            # Update sessions where archived_at is null
            cur.execute(
                """
                UPDATE sessions
                SET model = ?
                WHERE archived_at IS NULL
                  AND (provider_name = ? OR provider_name = 'inferhub' OR provider_name IS NULL OR provider_name = '')
                """,
                (new_model, target_provider),
            )
            return cur.rowcount
    finally:
        conn.close()


def resolve_notify_session(
    config_path: Path,
    db_path: Path = DEFAULT_DB_PATH,
    chat_id: str = INFERHUB_WATCH_CHAT_ID,
    thread_id: int = INFERHUB_WATCH_HQ_THREAD_ID,
) -> str:
    """Resolve the OpenCrabs session that should receive switch alerts (Issue #14).

    Order (dynamic, never hardcoded to a session id):
      1. Explicit override key `notify_session` in config.toml
         (top-level or [notifications]).
      2. The session bound to the HQ topic (`thread_id = 2`) of the Inferhub
         Watch group in the `session_bindings` table — preferring a binding
         whose session is still active (archived_at IS NULL), most recent first.
      3. Last resort: most recent binding row for that chat/topic, even if the
         bound session is archived (a stale binding is better than no alert).

    The lookup is scoped to the HQ topic, not just the group chat (Issue #21,
    Finding D): `session_bindings` is keyed on session_id with no
    (chat_id, thread_id) uniqueness, so a chat_id-only query lets whichever
    topic most recently received a message — a worker topic — steal the
    owner-facing switch alert from HQ topic 2.

    Raises RuntimeError (fail loudly) if no session can be resolved.
    """
    if config_path.exists():
        with config_path.open("rb") as f:
            cfg_data = tomllib.load(f)
        override = cfg_data.get("notify_session") or cfg_data.get("notifications", {}).get("notify_session")
        if override:
            return str(override)

    if not db_path.exists():
        raise RuntimeError(
            f"Cannot resolve notify session: bindings DB {db_path} does not exist "
            f"and no `notify_session` override is set in {config_path}."
        )

    conn = sqlite3.connect(str(db_path))
    try:
        # 2. Preferred: most recent binding for the chat/topic with an ACTIVE session.
        row = conn.execute(
            """
            SELECT b.session_id
            FROM session_bindings b
            JOIN sessions s ON s.id = b.session_id
            WHERE b.chat_id = ? AND b.thread_id = ? AND s.archived_at IS NULL
            ORDER BY b.updated_at DESC
            LIMIT 1
            """,
            (chat_id, thread_id),
        ).fetchone()
        if row:
            return str(row[0])

        # 3. Last resort: most recent binding for the chat/topic, regardless of
        #    session status (stale binding beats staying silent).
        row = conn.execute(
            """
            SELECT session_id
            FROM session_bindings
            WHERE chat_id = ? AND thread_id = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (chat_id, thread_id),
        ).fetchone()
        if row:
            logger.warning(
                f"No active session bound to chat {chat_id} topic {thread_id}; "
                f"falling back to most recent (possibly archived) binding {row[0]}."
            )
            return str(row[0])
    finally:
        conn.close()

    raise RuntimeError(
        f"No session resolvable for chat {chat_id} topic {thread_id}: no "
        f"`notify_session` override in {config_path} and no matching rows in "
        f"session_bindings ({db_path})."
    )


def send_session_notification(
    session_id: str,
    text: str,
    title: str = NOTIFY_TITLE,
    profile: str = NOTIFY_PROFILE,
    timeout_secs: int = 120,
) -> tuple[bool, str]:
    """Send an alert via the OpenCrabs CLI and verify the receipt (Issue #14).

    Runs: opencrabs -p <profile> session notify <session_id> --text ... --title ... --confirm

    Success requires BOTH: subprocess exit code 0 AND a --confirm verdict
    (woke / queued_pending_drain / delivered) in the CLI output. Both are
    logged. No silent HTTP fire: the notified session owns the Telegram card.
    """
    cmd = [
        "opencrabs",
        "-p", profile,
        "session", "notify", session_id,
        "--text", text,
        "--title", title,
        "--confirm",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_secs)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.error(f"session notify to {session_id} failed to run: {exc}")
        return False, f"error: {exc}"

    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    logger.info(f"session notify exit={proc.returncode} output={output!r}")
    # The confirm verdict is prose in the CLI detail line (see CONFIRM_VERDICTS).
    verdict = next((name for name, prose in CONFIRM_VERDICTS if prose in output), None)
    detail = f"exit={proc.returncode} verdict={verdict or 'none'} output={output!r}"

    if proc.returncode != 0 or verdict is None:
        logger.error(f"session notify to {session_id} NOT confirmed ({detail})")
        return False, detail

    logger.info(f"session notify to {session_id} confirmed ({detail})")
    return True, detail


def get_current_model(config_path: Path) -> str:
    """Read current default model from OpenCrabs config."""
    if not config_path.exists():
        return ""
    with config_path.open("rb") as f:
        data = tomllib.load(f)
    return (
        data.get("agent", {}).get("default_model")
        or data.get("providers", {}).get("custom", {}).get("inferhub", {}).get("default_model")
        or ""
    )


def gain_of(best: RouteCandidate | None, current: RouteCandidate | None) -> float:
    """Fractional value gain of ``best`` over ``current`` (0.0 when unknown)."""
    if best is None or current is None or current.value <= 0:
        return 0.0
    return (best.value - current.value) / current.value

def switcher_state_path(root_dir: Path = ROOT_DIR) -> Path:
    """Where the last real switch is recorded (dwell bookkeeping, #27)."""
    return Path(root_dir) / "data" / SWITCHER_STATE_NAME

def load_switcher_state(root_dir: Path = ROOT_DIR) -> dict[str, Any]:
    """Last real switch, or {} when never switched / state unreadable.

    An unreadable or absent state file must never BLOCK switching — a
    corrupt state file is not a reason to freeze the fleet — so every
    failure path returns an empty dict (no dwell recorded).
    """
    path = switcher_state_path(root_dir)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning(f"switcher state unreadable ({exc}); treating as no prior switch")
        return {}
    return data if isinstance(data, dict) else {}

def save_switcher_state(
    root_dir: Path,
    from_model: str,
    to_model: str,
    basis_mode: str,
    when: datetime | None = None,
) -> Path:
    """Record a real switch for dwell bookkeeping. Returns the state path."""
    when = when or datetime.now(timezone.utc)
    path = switcher_state_path(root_dir)
    payload = {
        "last_switch_at": when.isoformat(timespec="seconds"),
        "from_model": from_model,
        "to_model": to_model,
        "basis_mode": basis_mode,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path

def check_dwell_gate(
    root_dir: Path = ROOT_DIR,
    *,
    current_model: str | None = None,
    target_model: str | None = None,
    gain: float = 0.0,
    dwell_seconds: float = DWELL_SECONDS,
    override_gain: float = DWELL_OVERRIDE_GAIN,
    now: datetime | None = None,
) -> dict[str, Any]:
    """May a flip happen now, or must the fleet dwell? (issue #27 item 2)

    A flip is refused inside ``dwell_seconds`` of the last real switch
    unless the gain on the CONSISTENT basis exceeds ``override_gain``.
    Two flips 11 minutes apart — each migrating 1,294 sessions and
    resetting prompt-cache warmth — are what this gate exists to stop.

    Returns a dict with ``allowed`` (bool) and a ``detail`` line for the
    log/report; ``reason`` names the gate that decided.
    """
    now = now or datetime.now(timezone.utc)
    state = load_switcher_state(root_dir)
    last_raw = state.get("last_switch_at")
    if not last_raw:
        return {
            "allowed": True,
            "reason": "no_prior_switch",
            "elapsed_s": None,
            "detail": "no prior switch recorded; dwell gate open",
        }
    try:
        last = datetime.fromisoformat(str(last_raw))
    except ValueError:
        return {
            "allowed": True,
            "reason": "unparseable_state",
            "elapsed_s": None,
            "detail": f"state timestamp {last_raw!r} unparseable; dwell gate open",
        }
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    elapsed = (now - last).total_seconds()
    remaining = dwell_seconds - elapsed
    if elapsed >= dwell_seconds:
        return {
            "allowed": True,
            "reason": "dwell_elapsed",
            "elapsed_s": elapsed,
            "detail": (
                f"dwell elapsed ({elapsed/3600:.1f}h >= {dwell_seconds/3600:.1f}h since "
                f"{state.get('to_model')} @ {last_raw})"
            ),
        }
    if gain > override_gain:
        return {
            "allowed": True,
            "reason": "override_gain",
            "elapsed_s": elapsed,
            "detail": (
                f"dwell overridden: gain {gain*100:.0f}% > {override_gain*100:.0f}% "
                f"on the consistent basis ({elapsed/60:.0f}m into a "
                f"{dwell_seconds/3600:.1f}h dwell)"
            ),
        }
    return {
        "allowed": False,
        "reason": "dwell_active",
        "elapsed_s": elapsed,
        "detail": (
            f"last switch to {state.get('to_model')} was {elapsed/60:.0f}m ago; "
            f"dwell requires {dwell_seconds/3600:.1f}h and gain {gain*100:.0f}% "
            f"does not exceed the {override_gain*100:.0f}% override "
            f"({remaining/60:.0f}m remaining)"
        ),
    }

def run_auto_route_switch(
    root_dir: Path = ROOT_DIR,
    config_path: Path = DEFAULT_CONFIG_PATH,
    db_paths: list[Path] | None = None,
    dry_run: bool = False,
    notify: bool = True,
    notify_db_path: Path = DEFAULT_DB_PATH,
    notify_profile: str = NOTIFY_PROFILE,
    floor_iq: float = IQ_FLOOR,
    threshold: float = SWITCH_THRESHOLD,
    catalog_models: dict[str, dict] | None = None,
    use_db_catalog: bool = False,
    dwell_seconds: float = DWELL_SECONDS,
    override_gain: float = DWELL_OVERRIDE_GAIN,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Main pipeline execution for auto route switching."""
    if db_paths is None:
        db_paths = [DEFAULT_DB_PATH, DEFAULT_ROOT_DB_PATH]
    if now is None:
        now = datetime.now(timezone.utc)

    catalog_path = root_dir / "data" / "catalog.json"
    intel_path = root_dir / "data" / "intelligence.json"
    models_toml_path = root_dir / "models.toml"

    catalog_source = "provided" if catalog_models is not None else "file"
    if catalog_models is None and use_db_catalog:
        # Check Postgres route_metrics first for fresh hourly catalog asks (< 1h)
        try:
            db_models, max_updated = pgstore.load_route_metrics()
            if db_models and max_updated is not None:
                now_utc = datetime.now(timezone.utc)
                age_s = (now_utc - (max_updated if max_updated.tzinfo else max_updated.replace(tzinfo=timezone.utc))).total_seconds()
                if age_s <= 3600.0:
                    catalog_models = db_models
                    catalog_source = f"postgres_route_metrics (age: {age_s/60:.1f}m)"
                    logger.debug(f"Loaded {len(catalog_models)} fresh catalog models from Postgres route_metrics (age: {age_s/60:.1f}m)")
        except Exception as exc:
            logger.debug(f"Postgres route_metrics load skipped ({exc}); falling back to file.")

    if catalog_models is None:
        # Fallback to live API fetch if key available
        try:
            api_key = resolve_inferhub_key(config_path=config_path)
            if api_key:
                from probe.catalog import fetch_models
                live_models = fetch_models(api_key)
                if live_models:
                    catalog_models = live_models
                    catalog_source = "live_api"
                    logger.debug(f"Loaded {len(catalog_models)} fresh catalog models directly from live API")
        except Exception as exc:
            logger.debug(f"Live API catalog fetch skipped ({exc}); falling back to file.")

    if catalog_models is None:
        if not catalog_path.exists() or not intel_path.exists():
            return {"status": "error", "message": "Catalog or intelligence data missing"}
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        catalog_models = catalog.get("models", {})
        catalog_source = "data/catalog.json"
    else:
        if not intel_path.exists():
            return {"status": "error", "message": "Intelligence data missing"}

    intel = json.loads(intel_path.read_text(encoding="utf-8"))
    aa_map: dict[str, Any] = {}
    if models_toml_path.exists():
        aa_map = tomllib.loads(models_toml_path.read_text(encoding="utf-8")).get("aa", {})

    intel_slugs = intel.get("models", {})

    # Load pricing payload and snapshots if available
    pricing_path = root_dir / "data" / "pricing.json"
    pricing_payload = json.loads(pricing_path.read_text(encoding="utf-8")) if pricing_path.exists() else None

    # Refresh rolling 24h perf from live database usage logs if possible
    try:
        usage_rows = pgstore.window_rows(24)
        if usage_rows:
            fresh_perf = pricing.perf_stats(usage_rows)
            if pricing_payload is None:
                pricing_payload = {}
            pricing_payload["perf"] = fresh_perf
    except Exception as exc:
        logger.debug(f"Live pgstore perf fetch skipped ({exc}); using pricing_payload.")

    dated_snapshots = pricing.dated_snapshots(root_dir)
    qual_path = root_dir / "data" / "qual_runs.json"
    qual_runs = json.loads(qual_path.read_text(encoding="utf-8")) if qual_path.exists() else None

    # Check projection gate
    gate = official_compare.projection_gate(dated_snapshots)
    use_proj = gate.get("pass", False)

    # Initialize InferHubClient if API key is resolvable for live TPS candidate qualification
    client: InferHubClient | None = None
    if not dry_run:
        api_key = resolve_inferhub_key(config_path=config_path)
        if api_key:
            client = InferHubClient(api_key)

    current_model = get_current_model(config_path)
    diag: dict[str, Any] = {}
    should_switch, best, current, reason = find_best_route(
        catalog_models,
        intel_slugs,
        aa_map,
        current_model=current_model,
        floor_iq=floor_iq,
        threshold=threshold,
        pricing_payload=pricing_payload,
        dated_snapshots=dated_snapshots,
        qual_runs=qual_runs,
        use_proj=use_proj,
        client=client,
        qual_path=qual_path,
        out=diag,
    )
    basis_mode = diag.get("basis_mode", BASIS_FLOOR)

    # --- Issue #27 item 2: dwell gate --------------------------------------
    # A real switch is recorded in the state file; a second flip inside the
    # dwell window is refused unless the gain on the CONSISTENT basis clears
    # DWELL_OVERRIDE_GAIN. The thrash flipped twice in 11 minutes.
    dwell = check_dwell_gate(
        root_dir,
        current_model=current_model,
        target_model=best.route if best else None,
        gain=gain_of(best, current),
        dwell_seconds=dwell_seconds,
        override_gain=override_gain,
        now=now,
    )
    should_switch = should_switch and dwell["allowed"]

    result: dict[str, Any] = {
        "should_switch": should_switch,
        "current_model": current_model,
        "best_model": best.route if best else None,
        "reason": reason,
        "dry_run": dry_run,
        "config_updated": False,
        "sessions_updated": 0,
        "notification_sent": False,
        "catalog_source": catalog_source,
        "basis_mode": basis_mode,
        "basis_samples": diag.get("basis_samples", 0),
        "dwell": dwell,
    }
    if not dwell["allowed"]:
        result["reason"] = f"{reason} | DWELL GATE: {dwell['detail']}"
        logger.info(f"Dwell gate held the switch: {dwell['detail']}")

    if not should_switch or best is None:
        return result

    if dry_run:
        logger.info(f"[DRY-RUN] Would switch from {current_model} to {best.route}: {reason}")
        return result

    # 1. Update config.toml
    cfg_ok = update_opencrabs_config(config_path, best.route)
    result["config_updated"] = cfg_ok

    # 2. Update active sessions in databases
    total_sessions = 0
    for db_p in db_paths:
        if db_p.exists():
            cnt = update_active_sessions(db_p, best.route)
            total_sessions += cnt
    result["sessions_updated"] = total_sessions

    # 3. Record the switch for dwell bookkeeping (issue #27 item 2). This runs
    # BEFORE the notification so a notify failure cannot lose the dwell state.
    state_path = save_switcher_state(
        root_dir,
        from_model=current_model,
        to_model=best.route,
        basis_mode=basis_mode,
        when=now,
    )
    result["switcher_state_path"] = str(state_path)

    # 4. Notification via opencrabs session notify (Issue #14).
    if notify:
        gain_pct = ((best.value - current.value) / current.value * 100) if (current and current.value > 0) else 0.0
        msg = (
            f"🔀 *Inferhub Route Auto-Switched*\n\n"
            f"• *Old Model:* `{current_model}` (IQ={current.iq if current else 0:.1f}, In=${current.ask_in if current else 0:.5f}, Out=${current.ask_out if current else 0:.5f}, Val={current.value if current else 0:.1f})\n"
            f"• *New Model:* `{best.route}` (IQ={best.iq:.1f}, In=${best.ask_in:.5f}, Out=${best.ask_out:.5f}, Val={best.value:.1f})\n"
            f"• *Value Gain:* `+{gain_pct:.1f}%`\n"
            f"• *Basis:* `{basis_mode}` (both candidates on one basis, min {diag.get('basis_samples', 0)} streaming samples)\n"
            f"• *Dwell:* `{dwell['detail']}`\n"
            f"• *Active Sessions Migrated:* `{total_sessions}`\n"
            f"• *Config Updated:* `{config_path}`"
        )
        try:
            target_session = resolve_notify_session(config_path, db_path=notify_db_path)
        except RuntimeError as exc:
            # Fail loudly, but the switch itself already happened and stands.
            logger.error(str(exc))
            result["notification_sent"] = False
            result["notification_error"] = str(exc)
        else:
            result["notify_session"] = target_session
            sent, detail = send_session_notification(
                target_session, msg, title=NOTIFY_TITLE, profile=notify_profile
            )
            result["notification_sent"] = sent
            result["notification_detail"] = detail

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Auto route switching pipeline for Inferhub Watch")
    parser.add_argument("--dry-run", action="store_true", help="Evaluate candidates without modifying config or DB")
    parser.add_argument("--no-notify", action="store_true", help="Disable Telegram notifications")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="Path to config.toml")
    parser.add_argument("--floor-iq", type=float, default=IQ_FLOOR, help="Floor AA IQ score")
    parser.add_argument("--threshold", type=float, default=SWITCH_THRESHOLD, help="Gain threshold (0.15 = 15%%)")
    parser.add_argument("--use-db-catalog", action="store_true", help="Attempt loading live catalog from Postgres route_metrics")
    parser.add_argument("--dwell-hours", type=float, default=DWELL_SECONDS / 3600.0,
                        help="Minimum dwell after a real switch before another flip is eligible (issue #27)")
    parser.add_argument("--override-gain", type=float, default=DWELL_OVERRIDE_GAIN,
                        help="Gain on the consistent basis that overrides the dwell gate (3.0 = 300%%)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    res = run_auto_route_switch(
        config_path=args.config,
        dry_run=args.dry_run,
        notify=not args.no_notify,
        floor_iq=args.floor_iq,
        threshold=args.threshold,
        use_db_catalog=args.use_db_catalog,
        dwell_seconds=args.dwell_hours * 3600.0,
        override_gain=args.override_gain,
    )
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
