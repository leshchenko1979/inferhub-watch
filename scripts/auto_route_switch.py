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
- Switch alert via `opencrabs session notify` (Issue #14): the notified session owns the
  Telegram card; this script never touches Telegram or the Bot API directly.
"""

from __future__ import annotations

import argparse
import json
import logging
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

from scripts.sync_usage_logs import resolve_slug

logger = logging.getLogger("auto_route_switch")

DEFAULT_CONFIG_PATH = Path("/root/.opencrabs/profiles/ops/config.toml")
DEFAULT_DB_PATH = Path("/root/.opencrabs/profiles/ops/opencrabs.db")
DEFAULT_ROOT_DB_PATH = Path("/root/.opencrabs/opencrabs.db")
IQ_FLOOR = 35.0
SWITCH_THRESHOLD = 0.15  # >15% higher value

# Inferhub Watch Telegram group whose bound session receives switch alerts (Issue #14).
INFERHUB_WATCH_CHAT_ID = "-1004379632866"
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


def resolve_notify_session(
    config_path: Path,
    db_path: Path = DEFAULT_DB_PATH,
    chat_id: str = INFERHUB_WATCH_CHAT_ID,
) -> str:
    """Resolve the OpenCrabs session that should receive switch alerts (Issue #14).

    Order (dynamic, never hardcoded to a session id):
      1. Explicit override key `notify_session` in config.toml
         (top-level or [notifications]).
      2. The session bound to the Inferhub Watch group chat in the
         `session_bindings` table — preferring a binding whose session is
         still active (archived_at IS NULL), most recent first.
      3. Last resort: most recent binding row for that chat, even if the
         bound session is archived (a stale binding is better than no alert).

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
        # 2. Preferred: most recent binding for the chat with an ACTIVE session.
        row = conn.execute(
            """
            SELECT b.session_id
            FROM session_bindings b
            JOIN sessions s ON s.id = b.session_id
            WHERE b.chat_id = ? AND s.archived_at IS NULL
            ORDER BY b.updated_at DESC
            LIMIT 1
            """,
            (chat_id,),
        ).fetchone()
        if row:
            return str(row[0])

        # 3. Last resort: most recent binding for the chat, regardless of
        #    session status (stale binding beats staying silent).
        row = conn.execute(
            """
            SELECT session_id
            FROM session_bindings
            WHERE chat_id = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (chat_id,),
        ).fetchone()
        if row:
            logger.warning(
                f"No active session bound to chat {chat_id}; falling back to "
                f"most recent (possibly archived) binding {row[0]}."
            )
            return str(row[0])
    finally:
        conn.close()

    raise RuntimeError(
        f"No session resolvable for chat {chat_id}: no `notify_session` override "
        f"in {config_path} and no rows in session_bindings ({db_path})."
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

    # 3. Notification via opencrabs session notify (Issue #14).
    if notify:
        gain_pct = ((best.value - current.value) / current.value * 100) if (current and current.value > 0) else 0.0
        msg = (
            f"🔀 *Inferhub Route Auto-Switched*\n\n"
            f"• *Old Model:* `{current_model}` (IQ={current.iq if current else 0:.1f}, In=${current.ask_in if current else 0:.5f}, Out=${current.ask_out if current else 0:.5f}, Val={current.value if current else 0:.1f})\n"
            f"• *New Model:* `{best.route}` (IQ={best.iq:.1f}, In=${best.ask_in:.5f}, Out=${best.ask_out:.5f}, Val={best.value:.1f})\n"
            f"• *Value Gain:* `+{gain_pct:.1f}%`\n"
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
