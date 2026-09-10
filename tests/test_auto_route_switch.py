"""Unit tests for the automated route switching pipeline (Issue #12)."""

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import tomllib

from scripts.auto_route_switch import (
    RouteCandidate,
    calculate_value,
    evaluate_candidates,
    find_best_route,
    get_current_model,
    is_qualified,
    run_auto_route_switch,
    send_telegram_notification,
    update_active_sessions,
    update_opencrabs_config,
)


def test_calculate_value():
    # Value = IQ / (0.99 * ask_in + 0.01 * ask_out)
    # IQ = 39.4, ask_in = 0.00375, ask_out = 0.01875
    # denom = 0.99 * 0.00375 + 0.01 * 0.01875 = 0.0037125 + 0.0001875 = 0.0039
    # value = 39.4 / 0.0039 = 10102.564...
    val = calculate_value(39.4, 0.00375, 0.01875)
    assert round(val, 1) == 10102.6

    assert calculate_value(35.0, 0, 0) == 0.0
    assert calculate_value(35.0, -1, -1) == 0.0


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


@patch("urllib.request.urlopen")
def test_send_telegram_notification(mock_urlopen):
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.__enter__.return_value = mock_resp
    mock_urlopen.return_value = mock_resp

    res = send_telegram_notification("token123", 133526395, "test message")
    assert res is True
    assert mock_urlopen.called


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
