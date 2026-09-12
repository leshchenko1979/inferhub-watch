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
        _ttft, tps = stream_perf(500.0, 3000.0, {"completion_tokens": 0})
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

    def test_window_widens_when_24h_is_thin(self) -> None:
        # D5 (owner 2026-09-12): a route that cannot reach TPS_FLOOR valid
        # samples inside 24h has its window widened to the 7-day cap, so the
        # thin 24h slice no longer publishes null by accident.
        rows = [
            self._row("2026-09-05T09:00:00Z", 1000, 5000, 80),   # >24h before newest
            self._row("2026-09-06T10:00:00Z", 2000, 6000, 80),
        ]
        m = perf_stats(rows, window_hours=24)["models"]["m/a"]
        self.assertEqual(m["window_hours"], 168)
        self.assertEqual(m["window_reqs"], 2)      # both rows over the wide slice
        # reqs stays the REQUESTED window's count: the board's in-use bar and
        # the results pill both label it "newest 24h" (site/results.py:46).
        self.assertEqual(m["reqs"], 1)
        # ttft median over BOTH rows, oldest first -> (1000 + 2000) / 2
        self.assertEqual(m["ttft_p50_ms"], 1500.0)
        self.assertEqual(m["tps_samples"], 2)

    def test_window_stays_24h_when_samples_sufficient(self) -> None:
        # >= TPS_FLOOR valid samples inside 24h -> no widening, and the older
        # row stays out. Widening is a rescue for thin routes only.
        rows = [self._row(f"2026-09-06T1{i}:00:00Z", 1000, 5000, 80) for i in range(5)]
        rows.append(self._row("2026-09-05T09:00:00Z", 1000, 5000, 80))
        m = perf_stats(rows, window_hours=24)["models"]["m/a"]
        self.assertEqual(m["reqs"], 5)
        self.assertEqual(m["window_hours"], 24)
        self.assertEqual(m["tps_samples"], 5)
        # no widening -> no window_reqs key at all (absence is the signal)
        self.assertNotIn("window_reqs", m)

    def test_window_cap_holds_at_7_days(self) -> None:
        # The cap is a hard bound: a row older than 168h is never recovered,
        # however thin the recent slice is.
        rows = [
            self._row("2026-08-28T09:00:00Z", 1000, 5000, 80),   # >168h before newest
            self._row("2026-09-06T10:00:00Z", 2000, 6000, 80),
        ]
        m = perf_stats(rows, window_hours=24)["models"]["m/a"]
        self.assertEqual(m["reqs"], 1)
        self.assertEqual(m["window_hours"], 168)
        self.assertEqual(m["window_reqs"], 1)      # the cap recovered nothing
        self.assertEqual(m["ttft_p50_ms"], 2000.0)

    def test_widening_cannot_rescue_invalid_rows(self) -> None:
        # Widening looks further back for evidence, but it does not make a row
        # valid: a degenerate (batch / non-stream) row stays out of the mean
        # however wide the window, and tps_samples never exceeds the count of
        # genuinely valid rows.
        rows = [
            self._row("2026-09-04T10:00:00Z", 2273, 2275, 2000),   # 0.002s window
            self._row("2026-09-06T10:00:00Z", 2000, 6000, 80),
        ]
        m = perf_stats(rows, window_hours=24)["models"]["m/a"]
        self.assertEqual(m["window_hours"], 168)   # widened: only 1 valid sample
        self.assertEqual(m["window_reqs"], 2)      # both rows seen
        self.assertEqual(m["reqs"], 1)             # but only 1 in the requested 24h
        self.assertEqual(m["tps_samples"], 1)      # and only one is evidence

    def test_widening_is_per_model_not_global(self) -> None:
        # The window is chosen PER ROUTE. A fleet-wide cutoff would drag every
        # healthy model out to 7 days alongside the one thin route — which is
        # why the bucket-then-cut order exists.
        rows = [self._row(f"2026-09-06T1{i}:00:00Z", 1000, 5000, 80) for i in range(5)]
        rows.append(self._row("2026-09-05T09:00:00Z", 1000, 5000, 80, model="thin/b"))
        m = perf_stats(rows, window_hours=24)["models"]
        self.assertEqual(m["m/a"]["window_hours"], 24)
        self.assertEqual(m["thin/b"]["window_hours"], 168)

    def test_consumer_floor_still_bites_after_widening(self) -> None:
        # perf_stats publishes tps_mean from a single valid sample; the >= 5
        # floor is the CONSUMER's (D5 leaves it untouched). Pin that a widened
        # window yielding fewer than 5 samples still falls back to the fleet
        # reference rather than being accepted as production speed.
        import importlib.util
        spec = importlib.util.spec_from_file_location("psb", "scripts/predictor_scoreboard.py")
        psb = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(psb)
        thin = {"perf": {"models": {"r": {"tps_mean": 40.0, "tps_samples": 4}}}}
        self.assertEqual(psb._tps_of(thin, "r", 50.0), 50.0)      # below floor
        at = {"perf": {"models": {"r": {"tps_mean": 40.0, "tps_samples": 5}}}}
        self.assertEqual(psb._tps_of(at, "r", 50.0), 40.0)        # at floor

    def test_degenerate_and_implausible_tps_rows_skipped(self) -> None:
        # duration≈ttft (non-stream / queue-only timing) and >500 tps rows
        # would poison the mean — both must be skipped for tps but still
        # count in reqs and feed the ttft median.
        rows = [
            self._row("2026-09-06T10:00:00Z", 2273, 2275, 2000),   # 0.002s window
            self._row("2026-09-06T10:01:00Z", 500, 2000, 1200),    # 800 tps
            self._row("2026-09-06T10:02:00Z", 1500, 11000, 400),   # 41.7 tps, sane
        ]
        m = perf_stats(rows)["models"]["m/a"]
        self.assertEqual(m["reqs"], 3)
        self.assertEqual(m["tps_samples"], 1)
        self.assertAlmostEqual(m["tps_mean"], round(400 / 9.5, 1), places=6)

    def test_empty_rows(self) -> None:
        self.assertEqual(perf_stats([]), {"window_hours": 24, "models": {}})
