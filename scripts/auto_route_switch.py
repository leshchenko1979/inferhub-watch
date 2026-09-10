"""Automated route switching pipeline for Inferhub Watch (Issue #12).

Evaluates candidate routes against the current model using Artificial Analysis IQ
and live auction ask prices. Automatically switches the active route when a qualified
alternative offers >15% higher Value = IQ / (0.99 * ask_in + 0.01 * ask_out).

Criteria:
- AA IQ >= 35.0 (hard floor)
- Supports tool calling (`supports_tools` is True)
- DeepSeek-v4 guard: must have a date tag (e.g., -0731, -0813), while DeepSeek-v4.1+ is allowed
- Switch threshold: Candidate Value > Current Value * 1.15
- OpenCrabs updates: config.toml ([agent].default_model, [agent].subagent_model,
  [providers.custom.inferhub].default_model, and append to [providers.custom.inferhub].models)
  and active unarchived sessions in opencrabs.db
- Telegram notification with old vs new metrics.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomllib

# Add project root to sys.path
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.sync_usage_logs import resolve_slug

logger = logging.getLogger("auto_route_switch")

DEFAULT_CONFIG_PATH = Path("/root/.opencrabs/profiles/ops/config.toml")
DEFAULT_DB_PATH = Path("/root/.opencrabs/profiles/ops/opencrabs.db")
DEFAULT_ROOT_DB_PATH = Path("/root/.opencrabs/opencrabs.db")
IQ_FLOOR = 35.0
SWITCH_THRESHOLD = 0.15  # >15% higher value


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
    value: float
    supports_tools: bool
    supports_cache: bool


def calculate_value(iq: float, ask_in: float | None, ask_out: float | None) -> float:
    """Calculate empirical value metric = IQ / (0.99 * ask_in + 0.01 * ask_out)."""
    if ask_in is None or ask_out is None:
        return 0.0
    denom = (0.99 * ask_in) + (0.01 * ask_out)
    if denom <= 0:
        return 0.0
    return iq / denom


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
) -> dict[str, RouteCandidate]:
    """Evaluate and build RouteCandidate objects for all catalog models."""
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

        val = calculate_value(iq, ask_in, ask_out) if iq is not None else 0.0

        candidates[route] = RouteCandidate(
            route=route,
            iq=iq or 0.0,
            ask_in=ask_in,
            ask_out=ask_out,
            value=val,
            supports_tools=supports_tools,
            supports_cache=supports_cache,
        )
    return candidates


def find_best_route(
    catalog_models: dict[str, Any],
    intel_slugs: dict[str, Any],
    aa_map: dict[str, Any],
    current_model: str,
    floor_iq: float = IQ_FLOOR,
    threshold: float = SWITCH_THRESHOLD,
) -> tuple[bool, RouteCandidate | None, RouteCandidate | None, str]:
    """Determine if a route switch is needed.

    Returns:
        (should_switch, best_candidate, current_candidate, reason)
    """
    candidates = evaluate_candidates(catalog_models, intel_slugs, aa_map, floor_iq=floor_iq)
    current_cand = candidates.get(current_model)

    qualified_candidates = [
        c for r, c in candidates.items()
        if is_qualified(r, catalog_models.get(r, {}), c.iq if c.iq > 0 else None, floor_iq=floor_iq)
    ]

    if not qualified_candidates:
        return False, None, current_cand, "No qualified candidates found meeting IQ and tool requirements."

    # Sort descending by value
    qualified_candidates.sort(key=lambda c: c.value, reverse=True)
    best = qualified_candidates[0]

    if current_cand is None or current_cand.value <= 0:
        return True, best, current_cand, f"Current model {current_model} not evaluated or has 0 value; selecting top candidate {best.route}."

    gain = (best.value - current_cand.value) / current_cand.value
    if best.route != current_model and gain > threshold:
        return (
            True,
            best,
            current_cand,
            f"Candidate {best.route} (Val={best.value:.1f}) exceeds {current_model} (Val={current_cand.value:.1f}) by {gain * 100:.1f}% (threshold {threshold * 100:.0f}%).",
        )

    return (
        False,
        best,
        current_cand,
        f"Top candidate {best.route} gain over {current_model} is {gain * 100:.1f}% (<= {threshold * 100:.0f}% threshold).",
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


def send_telegram_notification(
    bot_token: str,
    chat_id: str | int,
    text: str,
    message_thread_id: int | None = None,
) -> bool:
    """Send switch notification to Telegram via Bot API."""
    if not bot_token or not chat_id:
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
    }
    if message_thread_id:
        payload["message_thread_id"] = message_thread_id

    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "InferhubWatch/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as exc:
        logger.error(f"Failed to send Telegram notification: {exc}")
        return False


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


def run_auto_route_switch(
    root_dir: Path = ROOT_DIR,
    config_path: Path = DEFAULT_CONFIG_PATH,
    db_paths: list[Path] | None = None,
    dry_run: bool = False,
    notify: bool = True,
    floor_iq: float = IQ_FLOOR,
    threshold: float = SWITCH_THRESHOLD,
    catalog_models: dict[str, dict] | None = None,
) -> dict[str, Any]:
    """Main pipeline execution for auto route switching."""
    if db_paths is None:
        db_paths = [DEFAULT_DB_PATH, DEFAULT_ROOT_DB_PATH]

    catalog_path = root_dir / "data" / "catalog.json"
    intel_path = root_dir / "data" / "intelligence.json"
    models_toml_path = root_dir / "models.toml"

    if catalog_models is None:
        if not catalog_path.exists() or not intel_path.exists():
            return {"status": "error", "message": "Catalog or intelligence data missing"}
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        catalog_models = catalog.get("models", {})
    else:
        if not intel_path.exists():
            return {"status": "error", "message": "Intelligence data missing"}

    intel = json.loads(intel_path.read_text(encoding="utf-8"))
    aa_map: dict[str, Any] = {}
    if models_toml_path.exists():
        aa_map = tomllib.loads(models_toml_path.read_text(encoding="utf-8")).get("aa", {})

    intel_slugs = intel.get("models", {})

    current_model = get_current_model(config_path)
    should_switch, best, current, reason = find_best_route(
        catalog_models,
        intel_slugs,
        aa_map,
        current_model=current_model,
        floor_iq=floor_iq,
        threshold=threshold,
    )

    result: dict[str, Any] = {
        "should_switch": should_switch,
        "current_model": current_model,
        "best_model": best.route if best else None,
        "reason": reason,
        "dry_run": dry_run,
        "config_updated": False,
        "sessions_updated": 0,
        "notification_sent": False,
    }

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

    # 3. Notification
    if notify and config_path.exists():
        with config_path.open("rb") as f:
            cfg_data = tomllib.load(f)
        bot_token = cfg_data.get("channels", {}).get("telegram", {}).get("token")
        if bot_token:
            gain_pct = ((best.value - current.value) / current.value * 100) if (current and current.value > 0) else 0.0
            msg = (
                f"🔀 *Inferhub Route Auto-Switched*\n\n"
                f"• *Old Model:* `{current_model}` (IQ={current.iq if current else 0:.1f}, In=${current.ask_in if current else 0:.5f}, Out=${current.ask_out if current else 0:.5f}, Val={current.value if current else 0:.1f})\n"
                f"• *New Model:* `{best.route}` (IQ={best.iq:.1f}, In=${best.ask_in:.5f}, Out=${best.ask_out:.5f}, Val={best.value:.1f})\n"
                f"• *Value Gain:* `+{gain_pct:.1f}%`\n"
                f"• *Active Sessions Migrated:* `{total_sessions}`\n"
                f"• *Config Updated:* `{config_path}`"
            )
            # Default admin chat / monitoring chat
            admin_chat = cfg_data.get("channels", {}).get("telegram", {}).get("admin_chat_id", 133526395)
            sent = send_telegram_notification(bot_token, admin_chat, msg)
            result["notification_sent"] = sent

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Auto route switching pipeline for Inferhub Watch")
    parser.add_argument("--dry-run", action="store_true", help="Evaluate candidates without modifying config or DB")
    parser.add_argument("--no-notify", action="store_true", help="Disable Telegram notifications")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="Path to config.toml")
    parser.add_argument("--floor-iq", type=float, default=IQ_FLOOR, help="Floor AA IQ score")
    parser.add_argument("--threshold", type=float, default=SWITCH_THRESHOLD, help="Gain threshold (0.15 = 15%%)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    res = run_auto_route_switch(
        config_path=args.config,
        dry_run=args.dry_run,
        notify=not args.no_notify,
        floor_iq=args.floor_iq,
        threshold=args.threshold,
    )
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
