"""stream_perf + result() perf fields: TTFT threading and TPS math."""

from __future__ import annotations

import unittest

from checks.core.check import stream_perf
from probe.result import result


class StreamPerfTest(unittest.TestCase):
    def test_tps_excludes_time_to_first_token(self) -> None:
        # 500 ms to first line, 2.5 s of generation, 100 tokens -> 40 tps.
        ttft, tps = stream_perf(500.0, 3000.0, {"completion_tokens": 100})
        self.assertEqual(ttft, 500.0)
        self.assertAlmostEqual(tps, 40.0)

    def test_no_ttft_error_path(self) -> None:
        self.assertEqual(stream_perf(None, 3000.0, {"completion_tokens": 100}), (None, None))

    def test_no_usage_tokens(self) -> None:
        ttft, tps = stream_perf(500.0, 3000.0, {})
        self.assertEqual(ttft, 500.0)
        self.assertIsNone(tps)

    def test_zero_completion_tokens(self) -> None:
        ttft, tps = stream_perf(500.0, 3000.0, {"completion_tokens": 0})
        self.assertIsNone(tps)

    def test_degenerate_window(self) -> None:
        # elapsed == ttft -> zero stream seconds -> no tps.
        ttft, tps = stream_perf(3000.0, 3000.0, {"completion_tokens": 100})
        self.assertEqual(ttft, 3000.0)
        self.assertIsNone(tps)

class ResultPerfFieldsTest(unittest.TestCase):
    def test_fields_round_and_default_none(self) -> None:
        cell = result(
            check_id="core", alias="a", status="pass", summary="s",
            latency_ms=1234.5, ttft_ms=226.44, tps=41.987,
        )
        self.assertEqual(cell["latency_ms"], 1234.5)
        self.assertEqual(cell["ttft_ms"], 226.4)
        self.assertEqual(cell["tps"], 41.99)

    def test_absent_perf_fields(self) -> None:
        cell = result(check_id="core", alias="a", status="pass", summary="s")
        self.assertIsNone(cell["ttft_ms"])
        self.assertIsNone(cell["tps"])

if __name__ == "__main__":
    unittest.main()
