#!/usr/bin/env python3
"""scan_opencrabs_timeouts.py — Lightweight OpenCrabs debug log scanner with stateful delta cursor.

Tracks provider timeouts, retry loops, and endpoint failures from OpenCrabs debug logs
(/root/.opencrabs/profiles/ops/logs/opencrabs.YYYY-MM-DD).

Key architecture requirements:
1. Low overhead byte-offset delta seeking (doesn't read already processed bytes).
2. Midnight date rotation handling (new date/filename, inode change -> reset offset).
3. In-place truncation handling by opencrabs-log-guard.sh (st_size < last_offset on same inode -> reset offset).
4. Correlates errors/retries/timeouts with models via session_id.
5. Saves persistent cursor in data/log_scan_state.json and summary in data/log_scan_summary.json.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_LOG_DIR = "/root/.opencrabs/profiles/ops/logs"
DEFAULT_STATE_FILE = "data/log_scan_state.json"
DEFAULT_SUMMARY_FILE = "data/log_scan_summary.json"

# Regex patterns for correlation and error matching
RE_SESSION = re.compile(r"session_id=([^\s,}]+)")
RE_MODEL_REQ = re.compile(
    r"run_tool_loop\{session_id=([^\s,}]+)\}.*?(?:streaming request: model=([^\s,]+)|Streaming call served:.*?model='([^']+)')"
)
RE_RETRY = re.compile(
    r"run_tool_loop\{session_id=([^\s,}]+)\}.*?Retry attempt (\d+/\d+).*?for error: (.*)"
)
RE_TIMEOUT_ERROR = re.compile(
    r"(timed out|deadline has elapsed|status: (?:504|408|502|503)|error sending request|request error|connection closed before message completed|connection reset by peer)",
    re.IGNORECASE,
)
RE_STREAM_RETRY = re.compile(
    r"run_tool_loop\{session_id=([^\s,}]+)\}.*?(?:Mid-stream error|Stream retry attempt).*?(?:timed out|deadline|error|HTTP|status)",
    re.IGNORECASE,
)
RE_WAIT_MS = re.compile(r"after\s*(\d+(?:\.\d+)?)\s*ms", re.IGNORECASE)
RE_WAIT_S = re.compile(r"(?:after:?|timed out after)\s*(\d+(?:\.\d+)?)\s*s\b", re.IGNORECASE)


def extract_wait_seconds(line: str, kind: str) -> float:
    """Extract wait time in seconds from log line or default sensibly."""
    m_s = RE_WAIT_S.search(line)
    if m_s:
        return float(m_s.group(1))

    m_ms = RE_WAIT_MS.search(line)
    if m_ms:
        return float(m_ms.group(1)) / 1000.0

    # Sensible defaults based on error kind
    if "timed out" in line.lower() or "deadline" in line.lower():
        return 60.0  # OpenCrabs HTTP stream timeout default is 60s
    if kind == "retry":
        return 1.0  # Backoff wait default ~1s
    return 5.0


def get_current_log_path(log_dir: str = DEFAULT_LOG_DIR) -> str:
    """Return path to today's UTC log file."""
    today_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    return os.path.join(log_dir, f"opencrabs.{today_utc}")


def load_state(state_path: str) -> Dict[str, Any]:
    """Load persistent cursor state."""
    if os.path.exists(state_path):
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Warning: failed to read state file {state_path}: {e}", file=sys.stderr)
    return {
        "log_path": "",
        "inode": 0,
        "last_offset": 0,
        "last_scan_utc": "",
        "sessions": {},  # session_id -> model route
    }


def save_state(state_path: str, state: Dict[str, Any]) -> None:
    """Save persistent cursor state atomically."""
    os.makedirs(os.path.dirname(os.path.abspath(state_path)), exist_ok=True)
    tmp_path = f"{state_path}.tmp.{os.getpid()}"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp_path, state_path)


def determine_seek_offset(
    log_path: str,
    st_ino: int,
    st_size: int,
    state: Dict[str, Any],
) -> Tuple[int, str]:
    """Determine starting seek offset and reason.

    Handles:
    - Fresh start
    - Midnight file rotation (st_ino != state['inode'] or path changed)
    - In-place truncation (st_size < state['last_offset'])
    - Normal delta resume (st_size >= state['last_offset'])
    """
    if state.get("log_path") != log_path:
        return 0, "path_changed_rotation"
    if state.get("inode") != st_ino:
        return 0, "inode_changed_rotation"
    last_offset = state.get("last_offset", 0)
    if st_size < last_offset:
        return 0, "inplace_truncation_detected"
    return last_offset, "normal_delta_seek"


def scan_log_delta(
    log_path: str,
    state_path: str = DEFAULT_STATE_FILE,
    max_bytes: Optional[int] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Scan log file delta starting from cursor offset.

    Returns:
        (events, scan_meta)
    """
    state = load_state(state_path)

    if not os.path.exists(log_path):
        return [], {"error": f"log file {log_path} does not exist"}

    stat_res = os.stat(log_path)
    st_ino = stat_res.st_ino
    st_size = stat_res.st_size

    offset, reason = determine_seek_offset(log_path, st_ino, st_size, state)

    # Session to model cache from state (retains recent sessions)
    session_models: Dict[str, str] = state.get("sessions", {})
    # Cap session cache to avoid unbounded growth
    if len(session_models) > 500:
        # Keep newest 250 items
        session_models = dict(list(session_models.items())[-250:])

    events: List[Dict[str, Any]] = []
    bytes_read = 0
    new_offset = offset

    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        if offset > 0:
            f.seek(offset)

        while True:
            line = f.readline()
            if not line:
                new_offset = f.tell()
                break

            bytes_read += len(line.encode("utf-8", errors="replace"))

            # Fast string pre-filter to avoid regex overhead on millions of debug lines
            has_model = "model=" in line
            has_err = (
                "timed out" in line
                or "error" in line
                or "Retry" in line
                or "status: 5" in line
                or "status: 4" in line
                or "deadline" in line
                or "connection" in line
            )

            if not (has_model or has_err):
                if max_bytes and bytes_read >= max_bytes:
                    new_offset = f.tell()
                    break
                continue

            # Extract session if present
            sess_match = RE_SESSION.search(line)
            session_id = sess_match.group(1) if sess_match else None

            # Track model requests to correlate sessions with models
            if has_model:
                req_match = RE_MODEL_REQ.search(line)
                if req_match:
                    s_id = req_match.group(1)
                    model = req_match.group(2) or req_match.group(3)
                    if s_id and model:
                        session_models[s_id] = model

            # Filter for timeouts / error markers
            if has_err:
                # Ignore bash tool execution timeouts (local shell commands, not provider hangs)
                if "Tool execution timed out after" in line:
                    continue

                err_match = RE_TIMEOUT_ERROR.search(line)
                retry_match = RE_RETRY.search(line)
                stream_match = RE_STREAM_RETRY.search(line)

                if err_match or retry_match or stream_match:
                    # Extract timestamp (standard ISO prefix: 2026-09-14T00:00:00...)
                    ts = line[:26] if len(line) >= 26 and line[10] == "T" else ""
                    model = session_models.get(session_id, "unknown") if session_id else "unknown"

                    if retry_match or stream_match:
                        kind = "retry"
                    else:
                        kind = "timeout_or_error"

                    wait_seconds = extract_wait_seconds(line, kind)

                    if retry_match:
                        detail = retry_match.group(3).strip()
                    elif stream_match:
                        detail = line[line.find("Mid-stream") if "Mid-stream" in line else line.find("Stream retry"):].strip()
                    elif err_match:
                        start_pos = err_match.start()
                        detail = line[start_pos:].strip()
                    else:
                        detail = ""

                    events.append({
                        "ts": ts,
                        "session_id": session_id,
                        "model": model,
                        "route": model,  # Canonical alias
                        "kind": kind,
                        "wait_seconds": wait_seconds,
                        "detail": detail,
                        "raw_line": line.strip()[:300],
                    })

            if max_bytes and bytes_read >= max_bytes:
                new_offset = f.tell()
                break

    # Update state
    now_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
    state["log_path"] = log_path
    state["inode"] = st_ino
    state["last_offset"] = new_offset
    state["last_scan_utc"] = now_utc
    state["sessions"] = session_models

    save_state(state_path, state)

    scan_meta = {
        "log_path": log_path,
        "inode": st_ino,
        "initial_offset": offset,
        "new_offset": new_offset,
        "seek_reason": reason,
        "bytes_scanned": new_offset - offset if new_offset >= offset else new_offset,
        "file_size": st_size,
        "events_found": len(events),
        "timestamp_utc": now_utc,
    }

    return events, scan_meta


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan OpenCrabs debug logs with delta cursor.")
    parser.add_argument("--log-path", default=None, help="Explicit log path (defaults to today UTC log)")
    parser.add_argument("--state-file", default=DEFAULT_STATE_FILE, help="State JSON path")
    parser.add_argument("--summary-file", default=DEFAULT_SUMMARY_FILE, help="Summary JSON output path")
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    args = parser.parse_args()

    log_path = args.log_path or get_current_log_path()
    events, meta = scan_log_delta(log_path, state_path=args.state_file)

    summary = {
        "meta": meta,
        "events_count": len(events),
        "events": events[-50:] if len(events) > 50 else events,  # tail up to 50
    }

    os.makedirs(os.path.dirname(os.path.abspath(args.summary_file)), exist_ok=True)
    with open(args.summary_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"Log scan complete: {meta['seek_reason']} (offset {meta['initial_offset']} -> {meta['new_offset']})")
        print(f"Bytes scanned: {meta['bytes_scanned']}, Events found: {len(events)}")
        if events:
            print("Recent events:")
            for ev in events[-5:]:
                print(f"  [{ev['ts']}] session={ev['session_id']} model={ev['model']} kind={ev['kind']}: {ev['detail'][:80]}")


if __name__ == "__main__":
    main()
