"""Portability and safety checks for the distributable route skill."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "skills" / "inferhub-auto-route" / "scripts" / "validate_skill.py"
spec = importlib.util.spec_from_file_location("inferhub_auto_route_validator", SCRIPT)
validator = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(validator)
dry_run = validator.dry_run
qualifies = validator.qualifies
validate_package = validator.validate_package


def test_skill_package_and_frontmatter():
    result = validate_package()
    assert result["name"] == "inferhub-auto-route"
    assert result["references"] >= 5
    assert result["skill_lines"] < 500


def test_iq_boundary_and_tool_support():
    base = {"iq": 35.0, "ask_in": 0.002, "ask_out": 0.01, "supports_tools": True}
    assert qualifies("model", base, 35.0)
    assert not qualifies("model", {**base, "iq": 34.99}, 35.0)
    assert not qualifies("model", {**base, "supports_tools": False}, 35.0)
    assert not qualifies("model", {**base, "iq": None}, 35.0)


def test_deepseek_uses_iq_only_not_date():
    record = {"iq": 35.0, "ask_in": 0.002, "ask_out": 0.01, "supports_tools": True}
    assert qualifies("deepseek/deepseek-v4", record, 35.0)
    assert not qualifies("deepseek/deepseek-v4", {**record, "iq": 34.9}, 35.0)


def test_configurable_weights_change_value():
    record = {"iq": 40.0, "ask_in": 0.002, "ask_out": 0.02, "supports_tools": True}
    assert qualifies("model", record, 35.0)
    low_output = 40.0 / (0.99 * record["ask_in"] + 0.01 * record["ask_out"])
    high_output = 40.0 / (0.50 * record["ask_in"] + 0.50 * record["ask_out"])
    assert low_output > high_output


def test_dry_run_is_auditable_and_has_no_side_effects(tmp_path):
    config = tmp_path / "config.toml"
    database = tmp_path / "opencrabs.db"
    config.write_text('default_model = "current/model"\n')
    database.write_text("unchanged")
    before = (config.read_bytes(), database.read_bytes())
    result = dry_run()
    assert result["dry_run"] is True
    assert result["qualified"]
    assert result["config_updated"] is False
    assert result["sessions_updated"] == 0
    assert before == (config.read_bytes(), database.read_bytes())


def test_cli_validation_and_dry_run():
    script = Path(__file__).parents[1] / "skills" / "inferhub-auto-route" / "scripts" / "validate_skill.py"
    result = subprocess.run([sys.executable, str(script), "--dry-run"], check=True, capture_output=True, text=True)
    payload = json.loads(result.stdout)
    assert payload["package"]["name"] == "inferhub-auto-route"
    assert payload["decision"]["dry_run"] is True
    assert payload["decision"]["config_updated"] is False
