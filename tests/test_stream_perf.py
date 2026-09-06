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

from probe.pricing import perf_stats

class PerfStatsTest(unittest.TestCase):
    def _row(self, ts, ttft, dur, out, model="m/a"):
        return {"ts": ts, "model": model, "ttft_ms": ttft,
                "duration_ms": dur, "completion_tokens": out}

    def test_medians_and_mean(self) -> None:
        rows = [
            self._row("2026-09-06T10:00:00Z", 1000, 5000, 80),
            self._row("2026-09-06T10:01:00Z", 2000, 6000, 80),
            self._row("2026-09-06T10:02:00Z", 1500, 4750, 65),
        ]
        perf = perf_stats(rows, window_hours=24)
        m = perf["models"]["m/a"]
        self.assertEqual(m["reqs"], 3)
        self.assertEqual(m["ttft_p50_ms"], 1500.0)
        # (80/4 + 80/4 + 65/3.25)/3
        self.assertAlmostEqual(m["tps_mean"], (20.0 + 20.0 + 20.0) / 3, places=6)

    def test_failed_and_untimed_rows_count_but_skip_medians(self) -> None:
        rows = [
            self._row("2026-09-06T10:00:00Z", 1000, 5000, 80),
            self._row("2026-09-06T10:01:00Z", None, None, 0),   # failed row
            {"ts": "2026-09-06T10:02:00Z", "model": "m/a"},      # bare row
        ]
        m = perf_stats(rows)["models"]["m/a"]
        self.assertEqual(m["reqs"], 3)
        self.assertEqual(m["ttft_p50_ms"], 1000.0)
        self.assertEqual(m.get("tps_samples"), 1)  # only the timed row feeds tps

    def test_window_slice_excludes_old_rows(self) -> None:
        rows = [
            self._row("2026-09-05T09:00:00Z", 1000, 5000, 80),   # >24h before newest
            self._row("2026-09-06T10:00:00Z", 2000, 6000, 80),
        ]
        m = perf_stats(rows, window_hours=24)["models"]["m/a"]
        self.assertEqual(m["reqs"], 1)
        self.assertEqual(m["ttft_p50_ms"], 2000.0)

    def test_empty_rows(self) -> None:
        self.assertEqual(perf_stats([]), {"window_hours": 24, "models": {}})
