"""Unit tests for the automated route switching pipeline (Issue #12)."""

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import tomllib

from scripts.auto_route_switch import (
    INFERHUB_WATCH_CHAT_ID,
    INFERHUB_WATCH_HQ_THREAD_ID,
    RouteCandidate,
    calculate_value,
    evaluate_candidates,
    find_best_route,
    get_current_model,
    is_qualified,
    resolve_notify_session,
    run_auto_route_switch,
    send_session_notification,
    update_active_sessions,
    update_opencrabs_config,
)


def test_calculate_value():
    # Value = (IQ / eff_price) * (tps / 50.0) ** 0.50
    # When using raw asks with default tps=50.0:
    # IQ = 39.4, ask_in = 0.00375, ask_out = 0.01875
    # denom = 0.99 * 0.00375 + 0.01 * 0.01875 = 0.0037125 + 0.0001875 = 0.0039
    # value = (39.4 / 0.0039) * (50/50)^0.50 = 10102.564...
    val = calculate_value(39.4, ask_in=0.00375, ask_out=0.01875)
    assert round(val, 1) == 10102.6

    # With eff_price and TPS weighting
    val_tps = calculate_value(39.4, eff_price=0.0039, tps=200.0)
    # (39.4 / 0.0039) * (200 / 50)**0.50 = 10102.564 * 2.0 = 20205.128...
    assert round(val_tps, 1) == 20205.1

    assert calculate_value(35.0, ask_in=0, ask_out=0) == 0.0
    assert calculate_value(35.0, ask_in=-1, ask_out=-1) == 0.0
    assert calculate_value(35.0, eff_price=0) == 0.0


def test_is_qualified_iq_floor():
    # Floor is 35.0
    info = {"supports_tools": True, "ask_in": 0.002, "ask_out": 0.01}
    assert is_qualified("test/model", info, 35.0) is True
    assert is_qualified("test/model", info, 40.0) is True
    assert is_qualified("test/model", info, 34.9) is False
    assert is_qualified("test/model", info, None) is False


def test_is_qualified_tools():
    info_no_tools = {"supports_tools": False, "ask_in": 0.002, "ask_out": 0.01}
    assert is_qualified("test/model", info_no_tools, 40.0) is False

    info_tools = {"supports_tools": True, "ask_in": 0.002, "ask_out": 0.01}
    assert is_qualified("test/model", info_tools, 40.0) is True


def test_is_qualified_deepseek_guard():
    info = {"supports_tools": True, "ask_in": 0.002, "ask_out": 0.01}
    # DeepSeek-v4 without date tag -> disqualified
    assert is_qualified("cbcn/deepseek-v4-flash", info, 40.0) is False
    assert is_qualified("deepseek/deepseek-v4", info, 40.0) is False

    # DeepSeek-v4 with date tag -> qualified
    assert is_qualified("cbcn/deepseek-v4-flash-0731", info, 40.0) is True
    assert is_qualified("cbcn/deepseek-v4-0813", info, 40.0) is True

    # DeepSeek-v4.1+ -> qualified without date tag
    assert is_qualified("cbcn/deepseek-v4.1-flash", info, 40.0) is True
    assert is_qualified("cbcn/deepseek-v4-1", info, 40.0) is True
    assert is_qualified("cbcn/deepseek-v4.2", info, 40.0) is True


def test_find_best_route_hysteresis():
    catalog_models = {
        "m_current": {"ask_in": 0.00375, "ask_out": 0.01875, "supports_tools": True},
        "m_better_10pct": {"ask_in": 0.0034, "ask_out": 0.017, "supports_tools": True},
        "m_better_30pct": {"ask_in": 0.0025, "ask_out": 0.0125, "supports_tools": True},
    }
    intel_slugs = {
        "m-current": {"iq": 39.4},
        "m-better-10pct": {"iq": 39.4},
        "m-better-30pct": {"iq": 39.4},
    }
    aa_map = {}

    # Test when candidate is only 10% better -> should NOT switch (threshold 15%)
    sub_catalog = {
        "m_current": catalog_models["m_current"],
        "m_better_10pct": catalog_models["m_better_10pct"],
    }
    should_switch, best, current, reason = find_best_route(
        sub_catalog, intel_slugs, aa_map, current_model="m_current", floor_iq=35.0, threshold=0.15
    )
    assert should_switch is False
    assert best.route == "m_better_10pct"

    # Test when candidate is >15% better -> SHOULD switch
    should_switch, best, current, reason = find_best_route(
        catalog_models, intel_slugs, aa_map, current_model="m_current", floor_iq=35.0, threshold=0.15
    )
    assert should_switch is True
    assert best.route == "m_better_30pct"


def test_update_opencrabs_config(tmp_path):
    config_content = """
[agent]
default_model = "ag/gemini-3.7-flash-high"
subagent_model = "ag/gemini-3.7-flash-high"

[providers.custom.inferhub]
default_model = "ag/gemini-3.7-flash-high"
models = ["ali/qwen3.8-max", "ag/gemini-3.7-flash-high"]
"""
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(config_content.strip(), encoding="utf-8")

    success = update_opencrabs_config(cfg_file, "cb/gpt-5.6-luna")
    assert success is True

    updated_data = tomllib.loads(cfg_file.read_text(encoding="utf-8"))
    assert updated_data["agent"]["default_model"] == "cb/gpt-5.6-luna"
    assert updated_data["agent"]["subagent_model"] == "cb/gpt-5.6-luna"
    assert updated_data["providers"]["custom"]["inferhub"]["default_model"] == "cb/gpt-5.6-luna"
    assert "cb/gpt-5.6-luna" in updated_data["providers"]["custom"]["inferhub"]["models"]
    assert "ali/qwen3.8-max" in updated_data["providers"]["custom"]["inferhub"]["models"]


def test_update_active_sessions(tmp_path):
    db_file = tmp_path / "test_opencrabs.db"
    conn = sqlite3.connect(str(db_file))
    with conn:
        conn.execute("""
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                model TEXT,
                provider_name TEXT,
                archived_at INTEGER
            )
        """)
        # 2 active inferhub sessions, 1 archived inferhub session, 1 active other provider session
        conn.execute("INSERT INTO sessions VALUES ('s1', 'old_model', 'custom:inferhub', NULL)")
        conn.execute("INSERT INTO sessions VALUES ('s2', 'old_model', 'inferhub', NULL)")
        conn.execute("INSERT INTO sessions VALUES ('s3', 'old_model', 'custom:inferhub', 12345678)")
        conn.execute("INSERT INTO sessions VALUES ('s4', 'openrouter_model', 'openrouter', NULL)")
    conn.close()

    updated = update_active_sessions(db_file, "cb/gpt-5.6-luna")
    assert updated == 2

    conn = sqlite3.connect(str(db_file))
    rows = conn.execute("SELECT id, model, archived_at FROM sessions ORDER BY id").fetchall()
    conn.close()

    assert rows[0] == ("s1", "cb/gpt-5.6-luna", None)
    assert rows[1] == ("s2", "cb/gpt-5.6-luna", None)
    assert rows[2] == ("s3", "old_model", 12345678)
    assert rows[3] == ("s4", "openrouter_model", None)


def _make_bindings_db(db_file, rows, thread_id=INFERHUB_WATCH_HQ_THREAD_ID):
    """Create a tmp DB with sessions + session_bindings tables.

    rows: list of (binding_session_id, chat_id, updated_at, archived_at_or_None)
    thread_id: forum topic the bindings belong to (defaults to the HQ topic).
    """
    conn = sqlite3.connect(str(db_file))
    with conn:
        conn.execute("""
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                model TEXT,
                provider_name TEXT,
                archived_at INTEGER,
                updated_at INTEGER
            )
        """)
        conn.execute("""
            CREATE TABLE session_bindings (
                session_id TEXT PRIMARY KEY,
                channel TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                thread_id INTEGER,
                updated_at INTEGER NOT NULL
            )
        """)
        for sid, chat, binding_ts, archived_at in rows:
            conn.execute(
                "INSERT INTO sessions VALUES (?, 'm', 'custom:inferhub', ?, ?)",
                (sid, archived_at, binding_ts),
            )
            conn.execute(
                "INSERT INTO session_bindings VALUES (?, 'telegram', ?, ?, ?)",
                (sid, chat, thread_id, binding_ts),
            )
    conn.close()



def test_run_auto_route_switch_end_to_end(tmp_path):
    # Setup mock data directory
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    catalog_json = {
        "models": {
            "current/m1": {"ask_in": 0.004, "ask_out": 0.02, "supports_tools": True},
            "better/m2": {"ask_in": 0.002, "ask_out": 0.01, "supports_tools": True},
        }
    }
    intelligence_json = {
        "models": {
            "m1": {"iq": 38.0},
            "m2": {"iq": 40.0},
        }
    }
    (data_dir / "catalog.json").write_text(json.dumps(catalog_json))
    (data_dir / "intelligence.json").write_text(json.dumps(intelligence_json))

    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("""
[agent]
default_model = "current/m1"
subagent_model = "current/m1"

[providers.custom.inferhub]
default_model = "current/m1"
models = ["current/m1"]
""")

    db_file = tmp_path / "opencrabs.db"
    conn = sqlite3.connect(str(db_file))
    with conn:
        conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, model TEXT, provider_name TEXT, archived_at INTEGER)")
        conn.execute("INSERT INTO sessions VALUES ('s1', 'current/m1', 'custom:inferhub', NULL)")
    conn.close()

    # Run dry run
    res_dry = run_auto_route_switch(
        root_dir=tmp_path,
        config_path=cfg_file,
        db_paths=[db_file],
        dry_run=True,
        notify=False,
    )
    assert res_dry["should_switch"] is True
    assert res_dry["best_model"] == "better/m2"
    assert res_dry["config_updated"] is False
    assert res_dry["sessions_updated"] == 0

    # Run actual switch
    res_live = run_auto_route_switch(
        root_dir=tmp_path,
        config_path=cfg_file,
        db_paths=[db_file],
        dry_run=False,
        notify=False,
    )
    assert res_live["should_switch"] is True
    assert res_live["best_model"] == "better/m2"
    assert res_live["config_updated"] is True
    assert res_live["sessions_updated"] == 1

    # Verify config and DB were updated
    cfg_data = tomllib.loads(cfg_file.read_text())
    assert cfg_data["agent"]["default_model"] == "better/m2"

    conn = sqlite3.connect(str(db_file))
    cur = conn.cursor()
    cur.execute("SELECT model FROM sessions WHERE id = 's1'")
    assert cur.fetchone()[0] == "better/m2"
    conn.close()


# --- Issue #14: opencrabs session notify alerts ---


def test_resolve_notify_session_override(tmp_path):
    # Explicit override key wins even when bindings exist
    cfg = tmp_path / "config.toml"
    cfg.write_text('notify_session = "override-session"\n')
    db = tmp_path / "opencrabs.db"
    _make_bindings_db(db, [("bound-session", INFERHUB_WATCH_CHAT_ID, 100, None)])
    assert resolve_notify_session(cfg, db_path=db) == "override-session"


def test_resolve_notify_session_override_notifications_section(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('[notifications]\nnotify_session = "notif-override"\n')
    assert resolve_notify_session(cfg, db_path=tmp_path / "missing.db") == "notif-override"


def test_resolve_notify_session_bindings_lookup(tmp_path):
    # No override: most recent ACTIVE binding for the watch chat wins
    cfg = tmp_path / "config.toml"
    cfg.write_text('[agent]\ndefault_model = "m"\n')
    db = tmp_path / "opencrabs.db"
    _make_bindings_db(db, [
        ("old-session", INFERHUB_WATCH_CHAT_ID, 100, None),
        ("new-session", INFERHUB_WATCH_CHAT_ID, 200, None),
        ("other-chat-session", "-999", 300, None),
    ])
    assert resolve_notify_session(cfg, db_path=db) == "new-session"


def test_resolve_notify_session_bindings_prefers_active(tmp_path):
    # Most recent binding points at an archived session: prefer older ACTIVE one
    cfg = tmp_path / "config.toml"
    cfg.write_text('[agent]\ndefault_model = "m"\n')
    db = tmp_path / "opencrabs.db"
    _make_bindings_db(db, [
        ("archived-session", INFERHUB_WATCH_CHAT_ID, 200, 12345),
        ("active-session", INFERHUB_WATCH_CHAT_ID, 100, None),
    ])
    assert resolve_notify_session(cfg, db_path=db) == "active-session"


def test_resolve_notify_session_last_resort_stale_binding(tmp_path):
    # Last resort: only binding is for an archived session -> still use it
    # (a stale binding beats staying silent)
    cfg = tmp_path / "config.toml"
    cfg.write_text('[agent]\ndefault_model = "m"\n')
    db = tmp_path / "opencrabs.db"
    _make_bindings_db(db, [("archived-session", INFERHUB_WATCH_CHAT_ID, 200, 12345)])
    assert resolve_notify_session(cfg, db_path=db) == "archived-session"

def test_resolve_notify_session_scoped_to_hq_topic_newer_worker_cannot_win(tmp_path):
    # Issue #21 Finding D: a newer binding in a WORKER topic must not steal the
    # owner-facing alert from HQ topic 2, even though its updated_at is highest.
    cfg = tmp_path / "config.toml"
    cfg.write_text('[agent]\ndefault_model = "m"\n')
    db = tmp_path / "opencrabs.db"
    _make_bindings_db(db, [("hq-session", INFERHUB_WATCH_CHAT_ID, 100, None)])
    # Same group chat, but a different forum topic (worker topic 510) whose
    # binding is strictly newer — the chat_id-only lookup would have picked it.
    conn = sqlite3.connect(str(db))
    with conn:
        conn.execute(
            "INSERT INTO sessions VALUES ('worker-session', 'm', 'custom:inferhub', NULL, 500)"
        )
        conn.execute(
            "INSERT INTO session_bindings VALUES ('worker-session', 'telegram', ?, 510, 500)",
            (INFERHUB_WATCH_CHAT_ID,),
        )
    conn.close()
    assert resolve_notify_session(cfg, db_path=db) == "hq-session"

def test_resolve_notify_session_ignores_worker_topic_when_hq_unbound(tmp_path):
    # Scoping holds for the last-resort leg too: with no HQ-topic binding at all,
    # a worker-topic binding must NOT be used (fail loudly rather than misroute).
    cfg = tmp_path / "config.toml"
    cfg.write_text('[agent]\ndefault_model = "m"\n')
    db = tmp_path / "opencrabs.db"
    _make_bindings_db(
        db, [("worker-session", INFERHUB_WATCH_CHAT_ID, 500, None)], thread_id=510
    )
    with pytest.raises(RuntimeError, match="No session resolvable"):
        resolve_notify_session(cfg, db_path=db)

def test_resolve_notify_session_loud_failure_no_bindings(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('[agent]\ndefault_model = "m"\n')
    db = tmp_path / "opencrabs.db"
    _make_bindings_db(db, [("other-chat", "-999", 100, None)])
    with pytest.raises(RuntimeError, match="No session resolvable"):
        resolve_notify_session(cfg, db_path=db)


def test_resolve_notify_session_loud_failure_missing_db(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('[agent]\ndefault_model = "m"\n')
    with pytest.raises(RuntimeError, match="does not exist"):
        resolve_notify_session(cfg, db_path=tmp_path / "missing.db")


def _mock_notify_proc(returncode=0, stdout="woke: session delivered", stderr=""):
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


@patch("scripts.auto_route_switch.subprocess.run")
def test_send_session_notification_success_woke(mock_run):
    # CLI text output for a confirm wake: outcome "delivered" + detail prose
    # "Confirmed end-to-end" (daemon confirm_route). Verdict reported = woke.
    mock_run.return_value = _mock_notify_proc(
        0, "✅ delivered: Confirmed end-to-end: the target was idle and has started a turn on the message."
    )
    ok, detail = send_session_notification("sess-1", "hello")
    assert ok is True
    assert "verdict=woke" in detail
    cmd = mock_run.call_args.args[0]
    assert cmd[:4] == ["opencrabs", "-p", "ops", "session"]
    assert "notify" in cmd and "sess-1" in cmd
    assert "--confirm" in cmd and "--text" in cmd and "--title" in cmd


@patch("scripts.auto_route_switch.subprocess.run")
def test_send_session_notification_verdict_queued_pending_drain(mock_run):
    # Mid-turn target: outcome "delivered" + detail prose "Confirmed queued".
    # Verdict must be reported as queued_pending_drain, not a bare "queued".
    mock_run.return_value = _mock_notify_proc(
        0, "✅ delivered: Confirmed queued: the target is mid-turn; the message injects at its next tool-loop boundary."
    )
    ok, detail = send_session_notification("sess-1", "hello")
    assert ok is True
    assert "verdict=queued_pending_drain" in detail


@patch("scripts.auto_route_switch.subprocess.run")
def test_send_session_notification_verdict_delivered(mock_run):
    # Routed but no wake observed within cap: detail prose "no wake was
    # observed" — still an accepted confirm verdict (delivered).
    mock_run.return_value = _mock_notify_proc(
        0, "✅ delivered: Routed to session sess-1, but no wake was observed within 10s — the target may be parked."
    )
    ok, detail = send_session_notification("sess-1", "hello")
    assert ok is True
    assert "verdict=delivered" in detail


@patch("scripts.auto_route_switch.subprocess.run")
def test_send_session_notification_nonzero_exit(mock_run):
    # exit != 0 -> failure even if output mentions a verdict word
    mock_run.return_value = _mock_notify_proc(2, "❌ no_route: 'x' is not a valid session UUID (exit 2)")
    ok, detail = send_session_notification("sess-1", "hello")
    assert ok is False
    assert "exit=2" in detail


@patch("scripts.auto_route_switch.subprocess.run")
def test_send_session_notification_missing_verdict(mock_run):
    # exit 0 but no confirm verdict prose: a PARKED outcome (#1206, channel
    # not claimed since boot) exits 0 yet carries no woke/queued_pending_drain/
    # delivered verdict — must NOT be treated as confirmed.
    mock_run.return_value = _mock_notify_proc(
        0, "✅ parked: queued for session sess-1: its channel has not claimed it since the last restart (#1206)"
    )
    ok, detail = send_session_notification("sess-1", "hello")
    assert ok is False
    assert "verdict=none" in detail


@patch("scripts.auto_route_switch.subprocess.run")
def test_send_session_notification_cli_missing(mock_run):
    mock_run.side_effect = OSError("opencrabs: command not found")
    ok, detail = send_session_notification("sess-1", "hello")
    assert ok is False
    assert detail.startswith("error:")


def _write_switch_fixtures(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "catalog.json").write_text(json.dumps({
        "models": {
            "current/m1": {"ask_in": 0.004, "ask_out": 0.02, "supports_tools": True},
            "better/m2": {"ask_in": 0.002, "ask_out": 0.01, "supports_tools": True},
        }
    }))
    (data_dir / "intelligence.json").write_text(json.dumps({
        "models": {"m1": {"iq": 38.0}, "m2": {"iq": 40.0}}
    }))
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("""
[agent]
default_model = "current/m1"
""")
    db_file = tmp_path / "opencrabs.db"
    conn = sqlite3.connect(str(db_file))
    with conn:
        conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, model TEXT, provider_name TEXT, archived_at INTEGER, updated_at INTEGER)")
        conn.execute("INSERT INTO sessions VALUES ('s1', 'current/m1', 'custom:inferhub', NULL, 1)")
        conn.execute("""
            CREATE TABLE session_bindings (
                session_id TEXT PRIMARY KEY,
                channel TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                thread_id INTEGER,
                updated_at INTEGER NOT NULL
            )
        """)
        conn.execute(
            "INSERT INTO session_bindings VALUES ('watch-session', 'telegram', ?, 2, 10)",
            (INFERHUB_WATCH_CHAT_ID,),
        )
    conn.close()
    return cfg_file, db_file


@patch("scripts.auto_route_switch.subprocess.run")
def test_run_auto_route_switch_notifies_via_session_notify(mock_run, tmp_path):
    cfg_file, db_file = _write_switch_fixtures(tmp_path)
    mock_run.return_value = _mock_notify_proc(
        0, "✅ delivered: Confirmed end-to-end: the target was idle and has started a turn on the message."
    )

    res = run_auto_route_switch(
        root_dir=tmp_path,
        config_path=cfg_file,
        db_paths=[],
        dry_run=False,
        notify=True,
        notify_db_path=db_file,
    )
    assert res["notification_sent"] is True
    assert res["notify_session"] == "watch-session"
    cmd = mock_run.call_args.args[0]
    assert "watch-session" in cmd
    assert "--confirm" in cmd
    # No raw Bot API surface anywhere in the command
    assert all("api.telegram.org" not in str(part) for part in cmd)


@patch("scripts.auto_route_switch.subprocess.run")
def test_run_auto_route_switch_notify_failure_switch_stands(mock_run, tmp_path):
    # Receipt not confirmed (non-zero exit): notification logged as failed,
    # but the switch itself still stands (config + sessions updated).
    cfg_file, db_file = _write_switch_fixtures(tmp_path)
    mock_run.return_value = _mock_notify_proc(2, "❌ no_route: 'x' is not a valid session UUID (exit 2)")

    res = run_auto_route_switch(
        root_dir=tmp_path,
        config_path=cfg_file,
        db_paths=[db_file],
        dry_run=False,
        notify=True,
        notify_db_path=db_file,
    )
    assert res["notification_sent"] is False
    assert "no_route" in res["notification_detail"]
    assert res["config_updated"] is True
    assert res["sessions_updated"] == 1

    conn = sqlite3.connect(str(db_file))
    cur = conn.cursor()
    cur.execute("SELECT model FROM sessions WHERE id = 's1'")
    assert cur.fetchone()[0] == "better/m2"
    conn.close()


def test_run_auto_route_switch_unresolvable_session_fails_loudly(tmp_path):
    # No override + no bindings for the watch chat: loud failure recorded,
    # switch still stands.
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "catalog.json").write_text(json.dumps({
        "models": {
            "current/m1": {"ask_in": 0.004, "ask_out": 0.02, "supports_tools": True},
            "better/m2": {"ask_in": 0.002, "ask_out": 0.01, "supports_tools": True},
        }
    }))
    (data_dir / "intelligence.json").write_text(json.dumps({
        "models": {"m1": {"iq": 38.0}, "m2": {"iq": 40.0}}
    }))
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[agent]\ndefault_model = "current/m1"\n')

    res = run_auto_route_switch(
        root_dir=tmp_path,
        config_path=cfg_file,
        db_paths=[],
        dry_run=False,
        notify=True,
        notify_db_path=tmp_path / "missing.db",
    )
    assert res["notification_sent"] is False
    assert "Cannot resolve notify session" in res["notification_error"]
    assert res["config_updated"] is True


def test_orthogonal_constraints():
    """Verify that IQ floor, tool support, and TPS/value calculation are completely orthogonal."""
    catalog_models = {
        "current/m1": {"ask_in": 0.004, "ask_out": 0.02, "supports_tools": True},
        # High TPS and cheap price, but fails IQ floor (< 35.0) -> must NOT qualify
        "sub_floor/m2": {"ask_in": 0.0001, "ask_out": 0.0005, "supports_tools": True},
        # High IQ and cheap price, but lacks tool support -> must NOT qualify
        "no_tools/m3": {"ask_in": 0.0001, "ask_out": 0.0005, "supports_tools": False},
        # Valid route meeting all orthogonal criteria
        "valid/m4": {"ask_in": 0.002, "ask_out": 0.01, "supports_tools": True},
    }
    intel_slugs = {
        "m1": {"iq": 38.0},
        "m2": {"iq": 32.0},  # Below 35.0 floor
        "m3": {"iq": 45.0},  # High IQ but no tools
        "m4": {"iq": 40.0},
    }
    aa_map = {}

    should_switch, best, current, reason = find_best_route(
        catalog_models, intel_slugs, aa_map, current_model="current/m1", floor_iq=35.0, threshold=0.15
    )
    assert should_switch is True
    assert best.route == "valid/m4"


def test_qualification_gates():
    """Verify individual qualification gates operate independently."""
    # Negative/zero pricing rejects
    assert is_qualified("m/zero_in", {"ask_in": 0.0, "ask_out": 0.01, "supports_tools": True}, 40.0) is False
    assert is_qualified("m/zero_out", {"ask_in": 0.01, "ask_out": 0.0, "supports_tools": True}, 40.0) is False
    # Non-tool naming patterns reject
    assert is_qualified("pub/whisper-large-v3", {"ask_in": 0.01, "ask_out": 0.01, "supports_tools": True}, 40.0) is False
    assert is_qualified("pub/flux-schnell", {"ask_in": 0.01, "ask_out": 0.01, "supports_tools": True}, 40.0) is False


def test_threshold_switch_hurdle():
    """Verify 15% switch hurdle threshold is respected."""
    # Exactly 15% or less gain does not trigger switch; >15% triggers switch
    catalog_models = {
        "current/m1": {"ask_in": 0.004, "ask_out": 0.02, "supports_tools": True},
        "better_14pct/m2": {"ask_in": 0.00355, "ask_out": 0.01775, "supports_tools": True},
        "better_16pct/m3": {"ask_in": 0.0034, "ask_out": 0.017, "supports_tools": True},
    }
    intel_slugs = {
        "m1": {"iq": 38.0},
        "m2": {"iq": 38.0},
        "m3": {"iq": 38.0},
    }
    aa_map = {}

    # 14% gain -> False
    sub_catalog = {
        "current/m1": catalog_models["current/m1"],
        "better_14pct/m2": catalog_models["better_14pct/m2"],
    }
    sw_14, b_14, _, _ = find_best_route(sub_catalog, intel_slugs, aa_map, current_model="current/m1", threshold=0.15)
    assert sw_14 is False

    # 16% gain -> True
    sub_catalog_16 = {
        "current/m1": catalog_models["current/m1"],
        "better_16pct/m3": catalog_models["better_16pct/m3"],
    }
    sw_16, b_16, _, _ = find_best_route(sub_catalog_16, intel_slugs, aa_map, current_model="current/m1", threshold=0.15)
    assert sw_16 is True
    assert b_16.route == "better_16pct/m3"

