"""Tests for site/freshness.py — P0b freshness gate and chip."""
from __future__ import annotations

import importlib.util
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from probe.registry import repo_root


def _load():
    path = repo_root() / "site" / "freshness.py"
    spec = importlib.util.spec_from_file_location("watch_freshness", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


freshness_mod = _load()
NOW = datetime(2026, 9, 7, 12, 0, 0, tzinfo=timezone.utc)


def _payload(age_hours: float) -> dict:
    return {"generated_at": (NOW - timedelta(hours=age_hours)).isoformat()}


class FreshnessTests(unittest.TestCase):
    def test_fresh_under_30h(self) -> None:
        info = freshness_mod.freshness(_payload(5), now=NOW)
        self.assertFalse(info["stale"])
        self.assertEqual(info["age_hours"], 5.0)
        self.assertEqual(info["label"], "5h old")

    def test_stale_beyond_30h(self) -> None:
        info = freshness_mod.freshness(_payload(31), now=NOW)
        self.assertTrue(info["stale"])
        self.assertEqual(info["label"], "1.3d old")

    def test_missing_generated_at_is_stale(self) -> None:
        info = freshness_mod.freshness({}, now=NOW)
        self.assertTrue(info["stale"])
        self.assertIsNone(info["age_hours"])

    def test_naive_timestamp_treated_as_utc(self) -> None:
        payload = {"generated_at": (NOW - timedelta(hours=2))
                   .replace(tzinfo=None).isoformat()}
        info = freshness_mod.freshness(payload, now=NOW)
        self.assertFalse(info["stale"])

    def test_boundary_exactly_30h_is_not_stale(self) -> None:
        info = freshness_mod.freshness(_payload(30), now=NOW)
        self.assertFalse(info["stale"])


class ChipTests(unittest.TestCase):
    def test_fresh_chip_class(self) -> None:
        html = freshness_mod.chip_html(_payload(2), now=NOW)
        self.assertIn("freshness-chip fresh", html)
        self.assertNotIn("stale", html.split('freshness-chip')[1][:30])

    def test_stale_chip_class(self) -> None:
        html = freshness_mod.chip_html(_payload(50), now=NOW)
        self.assertIn("freshness-chip stale", html)


class GateTests(unittest.TestCase):
    def test_gate_zero_when_fresh(self) -> None:
        self.assertEqual(freshness_mod.gate(_payload(2), now=NOW), 0)
        self.assertEqual(
            freshness_mod.gate(_payload(2), now=NOW, fail_stale=True), 0)

    def test_gate_warn_only_by_default(self) -> None:
        self.assertEqual(freshness_mod.gate(_payload(50), now=NOW), 0)

    def test_gate_fails_when_asked(self) -> None:
        self.assertEqual(
            freshness_mod.gate(_payload(50), now=NOW, fail_stale=True), 1)


if __name__ == "__main__":
    unittest.main()


class NonePayloadTests(unittest.TestCase):
    """pricing.json absent (load_pricing returns None) must not crash."""

    def test_chip_renders_stale_for_none(self) -> None:
        html = freshness_mod.chip_html(None, now=NOW)
        self.assertIn("freshness-chip stale", html)
        self.assertIn("age unknown", html)

    def test_gate_fails_none_when_asked(self) -> None:
        self.assertEqual(
            freshness_mod.gate(None, now=NOW, fail_stale=True), 1)


class WiringTests(unittest.TestCase):
    """generate.py main() gate behavior with a real pricing.json."""

    @staticmethod
    def _load_gen():
        import importlib.util
        path = repo_root() / "site" / "generate.py"
        spec = importlib.util.spec_from_file_location("watch_gen_fresh", path)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_main_passes_on_current_data(self) -> None:
        gen = self._load_gen()
        self.assertEqual(gen.main(), 0)

    def test_main_fails_when_freshness_fail_and_stale(self) -> None:
        import json, tempfile
        from unittest import mock
        gen = self._load_gen()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").mkdir()
            stale = {"generated_at": "2026-08-01T00:00:00+00:00", "routes": {}}
            (root / "data" / "pricing.json").write_text(json.dumps(stale))
            (root / "data" / "runs").mkdir()
            with mock.patch.object(gen, "ROOT", root), \
                    mock.patch.dict("os.environ", {"FRESHNESS_FAIL": "1"}):
                self.assertEqual(gen.main(), 1)
