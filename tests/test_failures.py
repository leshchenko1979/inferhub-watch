"""Tests for failure_stats() and the #pricing reliability sub-table."""

from __future__ import annotations

import unittest

from probe.pricing import failure_stats


def _row(model: str, *, status: str = "ok", http: int = 200) -> dict:
    return {"ts": "2026-09-01T00:00:00Z", "status": status, "http_status": http,
            "model": model, "prompt_tokens": 100, "completion_tokens": 10,
            "cached_tokens": 0, "cost_consumer_usdc": "0.001"}


class ErrorStatsTest(unittest.TestCase):
    """failure_stats(): counts, code breakdown, per-model rollup."""

    def test_all_ok(self):
        st = failure_stats([_row("m/a"), _row("m/a"), _row("m/b")])
        self.assertEqual(st["total"], 3)
        self.assertEqual(st["failed"], 0)
        self.assertEqual(st["rate_pct"], 0.0)
        self.assertEqual(st["codes"], {})
        self.assertEqual(st["by_model"]["m/a"], {"reqs": 2, "failed": 0, "codes": {}})

    def test_failed_rows_counted_with_codes(self):
        rows = [_row("m/a"), _row("m/a", status="failed", http=502),
                _row("m/a", status="failed", http=502),
                _row("m/b", status="failed", http=429)]
        st = failure_stats(rows)
        self.assertEqual(st["total"], 4)
        self.assertEqual(st["failed"], 3)
        self.assertEqual(st["rate_pct"], 75.0)
        self.assertEqual(st["codes"], {"502": 2, "429": 1})
        self.assertEqual(st["by_model"]["m/a"]["failed"], 2)
        self.assertEqual(st["by_model"]["m/a"]["codes"], {"502": 2})

    def test_models_sorted_by_failed_desc(self):
        st = failure_stats([_row("m/clean"), _row("m/broken", status="failed", http=502)])
        self.assertEqual(list(st["by_model"]), ["m/broken", "m/clean"])

    def test_null_http_becomes_unknown(self):
        row = _row("m/a", status="failed", http=502)
        row["http_status"] = None
        st = failure_stats([row])
        self.assertEqual(st["codes"], {"unknown": 1})

    def test_empty_rows(self):
        st = failure_stats([])
        self.assertEqual(st["total"], 0)
        self.assertIsNone(st["rate_pct"])

    def test_rows_without_model_skipped(self):
        st = failure_stats([{"ts": "x", "status": "failed", "http_status": 502, "model": ""}])
        self.assertEqual(st["total"], 1)  # window size, mirrors requests_scanned
        self.assertEqual(st["failed"], 0)  # unattributable row is not counted
        self.assertEqual(st["by_model"], {})
