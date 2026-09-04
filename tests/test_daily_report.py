"""Tests for probe.daily_report — offline: synthetic rows, tmp snapshots."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from probe import daily_report


def _row(model="ali/qwen3.8-max", tok_in=1000, tok_out=2000, cached=0,
         cost=0.05, status="ok", ts="2026-09-04T10:00:00Z"):
    return {
        "model": model,
        "ts": ts,
        "status": status,
        "prompt_tokens": tok_in,
        "completion_tokens": tok_out,
        "cached_tokens": cached,
        "cost_consumer_usdc": str(cost),
    }


def _snap(root: Path, day: str, routes: dict) -> None:
    payload = {
        "generated_at": f"{day}T06:15:00+00:00",
        "range": "30d",
        "requests_scanned": 10,
        "days": [{"date": day, "cost_usdc": "0.500000", "requests": 7}],
        "routes": routes,
    }
    (root / "data" / "pricing").mkdir(parents=True, exist_ok=True)
    (root / "data" / "pricing" / f"{day}.json").write_text(json.dumps(payload))


def _entry(ask_in, ask_out):
    return {"ask_in": ask_in, "ask_out": ask_out}


class Usage24hTest(unittest.TestCase):
    def test_aggregates_traffic_cache_and_cost(self):
        rows = [
            _row(tok_in=1000, tok_out=2000, cached=400, cost=0.10),
            _row(tok_in=100, tok_out=200, cached=0, cost=0.02),
        ]
        stats = daily_report.usage_24h(rows)
        agg = stats["ali/qwen3.8-max"]
        self.assertEqual(agg["reqs"], 2)
        self.assertEqual(agg["tok_in"], 1100)
        self.assertEqual(agg["tok_out"], 2200)
        self.assertEqual(agg["cached"], 400)
        self.assertAlmostEqual(agg["cost"], 0.12)

    def test_failed_rows_count_but_carry_no_tokens(self):
        rows = [
            _row(cost=0.10),
            _row(status="failed", tok_in=0, tok_out=0, cost=0),
        ]
        stats = daily_report.usage_24h(rows)
        agg = stats["ali/qwen3.8-max"]
        self.assertEqual(agg["reqs"], 2)
        self.assertEqual(agg["failed"], 1)
        self.assertEqual(agg["tok_in"], 1000)
        self.assertAlmostEqual(agg["cost"], 0.10)

    def test_rows_without_model_are_skipped(self):
        self.assertEqual(daily_report.usage_24h([_row(model="")]), {})


class PriceMovementsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_flags_real_move_and_ignores_noise_and_new_route(self):
        _snap(self.root, "2026-09-03", {
            "ali/kimi-k3": _entry(7.0, 21.0),
            "ali/qwen3.8-max": _entry(2.0, 8.0),
        })
        _snap(self.root, "2026-09-04", {
            "ali/kimi-k3": _entry(5.0, 21.0),
            "ali/qwen3.8-max": _entry(2.0 + 1e-12, 8.0),
            "cx/gpt-5.6-luna": _entry(1.0, 3.0),  # new route: not a move
        })
        moves = daily_report.price_movements(self.root)
        self.assertEqual(
            [(m["alias"], m["field"], m["was"], m["now"]) for m in moves],
            [("ali/kimi-k3", "ask_in", 7.0, 5.0)],
        )

    def test_needs_two_snapshots(self):
        _snap(self.root, "2026-09-04", {"ali/kimi-k3": _entry(7.0, 21.0)})
        self.assertEqual(daily_report.price_movements(self.root), [])


class BuildReportTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        _snap(self.root, "2026-09-03", {"ali/kimi-k3": _entry(7.0, 21.0)})
        _snap(self.root, "2026-09-04", {"ali/kimi-k3": _entry(5.0, 21.0)})
        rows = [_row(cached=500, cost=0.10)]

    def test_report_has_all_sections_and_no_key(self):
        rows = [_row(cached=500, cost=0.10)]
        with mock.patch.object(
            daily_report, "fetch_log_rows", return_value=rows
        ):
            text = daily_report.build_report("SECRET-KEY", self.root)
        self.assertIn("Inferhub daily", text)
        self.assertIn("Last 24h", text)
        self.assertIn("ali/qwen3.8-max", text)
        self.assertIn("Spend by UTC day", text)
        self.assertIn("Ask moves", text)
        self.assertIn("ali/kimi-k3", text)
        self.assertIn("-28.6%", text)
        self.assertNotIn("SECRET-KEY", text)

    def test_empty_window_still_renders(self):
        # same asks on both days: no movements expected
        _snap(self.root, "2026-09-04", {"ali/kimi-k3": _entry(7.0, 21.0)})
        with mock.patch.object(daily_report, "fetch_log_rows", return_value=[]):
            text = daily_report.build_report("SECRET-KEY", self.root)
        self.assertIn("No traffic in the window.", text)
        self.assertIn("No ask moves", text)


class LoadKeyTest(unittest.TestCase):
    def test_env_wins_over_keys_file(self):
        with mock.patch.dict(
            os.environ, {"INFERHUB_API_KEY": "env-key"}, clear=False
        ):
            self.assertEqual(daily_report.load_key(), "env-key")

    def test_missing_everywhere_returns_empty(self):
        with mock.patch.dict(
            os.environ, {}, clear=True
        ), mock.patch.object(
            daily_report, "KEYS_FILE", Path("/nonexistent/keys.toml")
        ):
            self.assertEqual(daily_report.load_key(), "")


if __name__ == "__main__":
    unittest.main()
