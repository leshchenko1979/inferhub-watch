"""Unit tests for provider timeout scanner, DB persistence, and True TPS discounting.

Issue #63 test coverage.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from probe import pgstore
from scripts import auto_route_switch
from scripts import scan_opencrabs_timeouts


class ProviderFailuresTests(unittest.TestCase):
    def test_extract_wait_seconds(self) -> None:
        # Retry with ms
        line1 = "2026-09-14T01:23:45.000000Z  WARN provider request failed, retrying after 931ms"
        self.assertAlmostEqual(scan_opencrabs_timeouts.extract_wait_seconds(line1, "retry"), 0.931)

        # Retry with s
        line2 = "2026-09-14T01:23:45.000000Z  WARN provider request failed, retrying after 2.5s"
        self.assertAlmostEqual(scan_opencrabs_timeouts.extract_wait_seconds(line2, "retry"), 2.5)

        # Timeout line with s
        line3 = "2026-09-14T01:23:45.000000Z ERROR provider request timed out after 60s"
        self.assertAlmostEqual(scan_opencrabs_timeouts.extract_wait_seconds(line3, "timeout"), 60.0)

        # Stream retry line with s
        line4 = "2026-09-14T01:23:45.000000Z  WARN stream failed mid-response; retrying after 3.0s"
        self.assertAlmostEqual(scan_opencrabs_timeouts.extract_wait_seconds(line4, "stream_retry"), 3.0)

        # Default fallback for timeout
        line_fallback = "2026-09-14T01:23:45.000000Z ERROR timed out with unknown format"
        self.assertEqual(scan_opencrabs_timeouts.extract_wait_seconds(line_fallback, "timeout"), 60.0)

    def test_calculate_true_tps(self) -> None:
        # Zero failures / wait: True TPS equals raw TPS, penalty is 1.0
        tps, penalty = auto_route_switch.calculate_true_tps(raw_tps=50.0, failure_count=0, wait_seconds=0.0)
        self.assertEqual(tps, 50.0)
        self.assertEqual(penalty, 1.0)

        # Failures with wait seconds (scaled by total_requests and active duration)
        tps_disc, penalty_disc = auto_route_switch.calculate_true_tps(
            raw_tps=50.0, failure_count=5, wait_seconds=120.0, total_requests=100, active_seconds=500.0
        )
        self.assertLess(tps_disc, 50.0)
        self.assertLess(penalty_disc, 1.0)
        self.assertGreater(tps_disc, 0.0)

        # High volume low failure rate has mild penalty (e.g. 10 failures out of 10,000 requests)
        tps_mild, pen_mild = auto_route_switch.calculate_true_tps(
            raw_tps=100.0, failure_count=10, wait_seconds=60.0, total_requests=10000, active_seconds=50000.0
        )
        self.assertGreater(pen_mild, 0.95)
        self.assertGreater(tps_mild, 95.0)

        # High failure rate (e.g. 50 failures out of 50 requests) hits floor clamp
        tps_severe, pen_severe = auto_route_switch.calculate_true_tps(
            raw_tps=100.0, failure_count=50, wait_seconds=3000.0, total_requests=0, active_seconds=0.0
        )
        self.assertEqual(pen_severe, 0.10)
        self.assertEqual(tps_severe, 10.0)

        # Zero or negative raw TPS returns 0.0
        tps_zero, penalty_zero = auto_route_switch.calculate_true_tps(raw_tps=0.0, failure_count=5, wait_seconds=60.0)
        self.assertEqual(tps_zero, 0.0)

    def test_insert_and_load_provider_failures(self) -> None:
        conn = MagicMock()
        cur = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cur

        events = [
            {
                "ts": "2026-09-14T01:00:00+00:00",
                "kind": "timeout",
                "route": "ag/gemini-3.8-flash-high",
                "session_id": "1122b15e-0000-0000-0000-000000000000",
                "wait_seconds": 60.0,
                "detail": "timed out after 60s",
                "raw_line": "raw log line",
            }
        ]

        inserted = pgstore.insert_provider_failures(conn, events)
        self.assertEqual(inserted, 1)
        self.assertTrue(cur.execute.called)

        # Mock loading 24h failures
        cur.fetchall.return_value = [
            ("ag/gemini-3.8-flash-high", 3, 120.0, 500, 2500.0)
        ]
        stats = pgstore.load_24h_provider_failures(conn)
        self.assertIn("ag/gemini-3.8-flash-high", stats)
        self.assertEqual(stats["ag/gemini-3.8-flash-high"]["failures"], 3)
        self.assertEqual(stats["ag/gemini-3.8-flash-high"]["wait_seconds"], 120.0)
        self.assertEqual(stats["ag/gemini-3.8-flash-high"]["total_requests"], 500)
        self.assertEqual(stats["ag/gemini-3.8-flash-high"]["active_seconds"], 2500.0)


    def test_evaluate_candidates_applies_failure_discount(self) -> None:
        catalog_models = {
            "ag/gemini-3.8-flash-high": {
                "ask_in": 0.0001,
                "ask_out": 0.0004,
                "supports_tools": True,
                "supports_cache": False,
            }
        }
        aa_map = {"ag/gemini-3.8-flash-high": "gemini-3-8-flash"}
        intel_slugs = {"gemini-3-8-flash": {"iq": 120.0}}

        # Clean run without failures
        clean_cands = auto_route_switch.evaluate_candidates(
            catalog_models=catalog_models,
            aa_map=aa_map,
            intel_slugs=intel_slugs,
            failure_stats={},
        )
        clean_cand = clean_cands["ag/gemini-3.8-flash-high"]
        self.assertEqual(clean_cand.failure_count, 0)
        self.assertEqual(clean_cand.timeout_wait_s, 0.0)
        self.assertEqual(clean_cand.failure_penalty, 1.0)
        self.assertEqual(clean_cand.tps, clean_cand.raw_tps)

        # Penalized run with failures
        failure_stats = {
            "ag/gemini-3.8-flash-high": {
                "failures": 4,
                "wait_seconds": 60.0,
            }
        }
        penalized_cands = auto_route_switch.evaluate_candidates(
            catalog_models=catalog_models,
            aa_map=aa_map,
            intel_slugs=intel_slugs,
            failure_stats=failure_stats,
        )
        penalized_cand = penalized_cands["ag/gemini-3.8-flash-high"]
        self.assertEqual(penalized_cand.failure_count, 4)
        self.assertEqual(penalized_cand.timeout_wait_s, 60.0)
        self.assertLess(penalized_cand.failure_penalty, 1.0)
        self.assertLess(penalized_cand.tps, penalized_cand.raw_tps)
        self.assertLess(penalized_cand.value, clean_cand.value)


if __name__ == "__main__":
    unittest.main()
