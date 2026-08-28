"""Unit tests for probe/pricing.py — no network; urlopen is mocked."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import probe.pricing as pricing


def _row(ts="2026-08-27T10:00:00Z", model="cp/xai/grok-4.6", **kw):
    row = {
        "ts": ts,
        "model": model,
        "status": "ok",
        "prompt_tokens": 1000,
        "completion_tokens": 100,
        "cached_tokens": 0,
        "cost_consumer_usdc": "0.001",
        "ask_input_per_mtok": "1",
        "ask_output_per_mtok": "3",
    }
    row.update(kw)
    return row


class AggregateTests(unittest.TestCase):
    def test_totals_and_latest_ask_win(self) -> None:
        rows = [
            _row(ts="2026-08-27T10:00:00Z", ask_input_per_mtok="2", cost_consumer_usdc="0.001"),
            _row(ts="2026-08-27T09:00:00Z", ask_input_per_mtok="9", cost_consumer_usdc="0.002"),
        ]
        agg = pricing.aggregate_rows(rows)["cp/xai/grok-4.6"]
        self.assertEqual(agg["reqs"], 2)
        self.assertEqual(agg["tok_in"], 2000)
        self.assertEqual(agg["tok_out"], 200)
        self.assertEqual(agg["cost"], 0.003)
        self.assertEqual(agg["ask_in"], 2.0)  # latest row, not first-seen
        self.assertEqual(agg["last_ts"], "2026-08-27T10:00:00Z")

    def test_cached_tokens_sum(self) -> None:
        agg = pricing.aggregate_rows([_row(cached_tokens=500), _row(cached_tokens=250)])
        self.assertEqual(agg["cp/xai/grok-4.6"]["cached"], 750)

    def test_rows_without_model_are_skipped(self) -> None:
        self.assertEqual(pricing.aggregate_rows([_row(model="")]), {})

    def test_bad_numbers_do_not_crash(self) -> None:
        agg = pricing.aggregate_rows([_row(cost_consumer_usdc=None, ask_input_per_mtok="x")])
        entry = agg["cp/xai/grok-4.6"]
        self.assertEqual(entry["cost"], 0.0)
        self.assertIsNone(entry["ask_in"])


class RouteEntryTests(unittest.TestCase):
    def test_effective_price_and_cache_pct(self) -> None:
        stats = {
            "reqs": 2,
            "tok_in": 2000,
            "tok_out": 0,
            "cached": 1000,
            "cost": 0.0009,
            "ask_in": 1.0,
            "ask_out": 3.0,
            "last_ts": "t",
        }
        entry = pricing.route_entry(stats, {}, "cp/xai/grok-4.6")
        self.assertEqual(entry["source"], "usage-logs")
        self.assertEqual(entry["eff_per_mtok"], 0.45)  # 0.0009 / 2000 * 1e6
        self.assertEqual(entry["cache_pct"], 50.0)
        self.assertEqual(entry["cost_usdc"], "0.000900")

    def test_catalog_fallback_when_no_rows(self) -> None:
        entry = pricing.route_entry(None, {"zai/glm-5.3": (0.045, 0.15)}, "zai/glm-5.3")
        self.assertEqual(entry["source"], "catalog")
        self.assertEqual(entry["ask_in"], 0.045)
        self.assertIsNone(entry["eff_per_mtok"])

    def test_unknown_route_marks_none(self) -> None:
        entry = pricing.route_entry(None, {}, "ghost/model")
        self.assertEqual(entry["source"], "none")


class FetchCatalogTests(unittest.TestCase):
    def test_parses_prefix_model_and_min_ask(self) -> None:
        body = [
            {
                "prefix": "cp",
                "enabled": True,
                "models": [
                    {
                        "upstreamModelId": "xai/grok-4.6",
                        "enabled": True,
                        "asksIn": [0.9, 0.5],
                        "asksOut": [2.7, 1.5],
                    },
                    {"upstreamModelId": "dead", "enabled": False, "asksIn": [1], "asksOut": [1]},
                ],
            }
        ]
        with mock.patch.object(pricing, "_get", return_value=body):
            asks = pricing.fetch_catalog("k")
        self.assertEqual(asks["cp/xai/grok-4.6"], (0.5, 1.5))
        self.assertNotIn("cp/dead", asks)


class SnapshotTests(unittest.TestCase):
    def test_snapshot_merges_logs_and_catalog(self) -> None:
        rows = [_row(model="cp/xai/grok-4.6", prompt_tokens=100, completion_tokens=0,
                     cached_tokens=0, cost_consumer_usdc="0.0001")]
        with mock.patch.object(pricing, "fetch_log_rows", return_value=rows) as log_mock, \
                mock.patch.object(pricing, "fetch_catalog",
                                  return_value={"zai/glm-5.3": (0.045, 0.15)}):
            payload = pricing.snapshot("k", ["cp/xai/grok-4.6", "zai/glm-5.3"])
        self.assertEqual(log_mock.call_args.kwargs["max_pages"], pricing.MAX_PAGES)
        self.assertEqual(payload["routes"]["cp/xai/grok-4.6"]["source"], "usage-logs")
        self.assertEqual(payload["routes"]["zai/glm-5.3"]["source"], "catalog")
        self.assertEqual(payload["requests_scanned"], 1)

    def test_snapshot_includes_days_series(self) -> None:
        rows = [_row(ts="2026-08-26T10:00:00Z"), _row(ts="2026-08-27T10:00:00Z")]
        with mock.patch.object(pricing, "fetch_log_rows", return_value=rows), \
                mock.patch.object(pricing, "fetch_catalog", return_value={}):
            payload = pricing.snapshot("k", ["cp/xai/grok-4.6"])
        self.assertEqual(
            [d["date"] for d in payload["days"]], ["2026-08-26", "2026-08-27"]
        )
        self.assertEqual(payload["days"][1]["requests"], 1)

    def test_main_writes_file_and_survives_failure(self) -> None:
        payload = {"generated_at": "t", "routes": {}}
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict("os.environ", {"INFERHUB_API_KEY": "k"}), \
                    mock.patch.object(pricing, "load_aliases", return_value=["a/b"]), \
                    mock.patch.object(pricing, "snapshot", return_value=payload), \
                    mock.patch.object(pricing, "repo_root", return_value=Path(tmp)):
                self.assertEqual(pricing.main(), 0)
                self.assertTrue((Path(tmp) / "data" / "pricing.json").is_file())
            with mock.patch.dict("os.environ", {"INFERHUB_API_KEY": "k"}), \
                    mock.patch.object(pricing, "load_aliases", return_value=["a/b"]), \
                    mock.patch.object(
                        pricing, "snapshot", side_effect=RuntimeError("boom")
                    ), mock.patch.object(
                        pricing, "repo_root", return_value=Path(tmp)
                    ), mock.patch.object(pricing.time, "sleep") as sleep_mock, \
                    mock.patch("sys.stderr", new=io.StringIO()), \
                    mock.patch("sys.stdout", new=io.StringIO()) as out:
                self.assertEqual(pricing.main(), 0)  # keeps previous file, exits clean
                self.assertEqual(sleep_mock.call_count, pricing.ATTEMPTS - 1)
                self.assertIn("::warning::", out.getvalue())  # staleness is visible in CI

    def test_main_without_key_is_a_noop(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(pricing.main(), 0)


class RetryTests(unittest.TestCase):
    def test_transient_429_is_retried_and_succeeds(self) -> None:
        from urllib.error import HTTPError

        payload = {"generated_at": "t2", "routes": {}}
        boom = HTTPError("https://management.example", 429, "Too Many Requests", {}, None)
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict("os.environ", {"INFERHUB_API_KEY": "k"}), \
                    mock.patch.object(pricing, "load_aliases", return_value=["a/b"]), \
                    mock.patch.object(pricing, "snapshot", side_effect=[boom, payload]), \
                    mock.patch.object(pricing, "repo_root", return_value=Path(tmp)), \
                    mock.patch.object(pricing.time, "sleep") as sleep_mock, \
                    mock.patch("sys.stderr", new=io.StringIO()):
                self.assertEqual(pricing.main(), 0)
            self.assertEqual(sleep_mock.call_count, 1)  # one backoff, then success
            self.assertEqual(sleep_mock.call_args.args[0], pricing.RETRY_BACKOFF_S)
            written = json.loads((Path(tmp) / "data" / "pricing.json").read_text())
            self.assertEqual(written["generated_at"], "t2")


class DailySeriesTests(unittest.TestCase):
    def test_aggregates_per_utc_day_oldest_first(self) -> None:
        rows = [
            _row(ts="2026-08-27T10:00:00Z", cost_consumer_usdc="0.003"),
            _row(ts="2026-08-26T23:59:59Z", cost_consumer_usdc="0.001"),
            _row(ts="2026-08-27T01:00:00Z", cost_consumer_usdc="0.002"),
        ]
        series = pricing.daily_series(rows)
        self.assertEqual([d["date"] for d in series], ["2026-08-26", "2026-08-27"])
        self.assertEqual(series[0]["requests"], 1)
        self.assertEqual(series[0]["cost_usdc"], "0.001000")
        self.assertEqual(series[1]["requests"], 2)
        self.assertEqual(series[1]["cost_usdc"], "0.005000")

    def test_rows_without_ts_are_skipped(self) -> None:
        self.assertEqual(pricing.daily_series([_row(ts=""), _row(ts=None)]), [])

    def test_bad_cost_counts_as_zero(self) -> None:
        series = pricing.daily_series([_row(cost_consumer_usdc="junk")])
        self.assertEqual(series[0]["cost_usdc"], "0.000000")
        self.assertEqual(series[0]["requests"], 1)


class LatestRunCandidatesTests(unittest.TestCase):
    def _write_run(self, tmp: str, name: str, payload: dict) -> None:
        runs = Path(tmp) / "data" / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        (runs / name).write_text(json.dumps(payload))

    def test_missing_dir_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(pricing.latest_run_candidates(Path(tmp)), [])

    def test_candidates_key_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._write_run(tmp, "2026-08-27T060000Z.json", {"candidates": ["old/m"]})
            self._write_run(
                tmp, "2026-08-28T060000Z.json",
                {"candidates": ["c/m", "d/m"], "cells": []},
            )
            self.assertEqual(pricing.latest_run_candidates(Path(tmp)), ["c/m", "d/m"])

    def test_legacy_run_falls_back_to_candidate_cells(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._write_run(tmp, "2026-08-28T060000Z.json", {"cells": [
                {"alias": "a/m", "check_id": "core"},
                {"alias": "c/m", "check_id": "core", "candidate": True},
                {"alias": "c/m", "check_id": "cache", "candidate": True},
            ]})
            self.assertEqual(pricing.latest_run_candidates(Path(tmp)), ["c/m"])

    def test_unreadable_latest_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._write_run(tmp, "2026-08-28T060000Z.json", {})
            (Path(tmp) / "data" / "runs" / "2026-08-28T060000Z.json").write_text("{broken")
            self.assertEqual(pricing.latest_run_candidates(Path(tmp)), [])


class SnapshotRoutesTests(unittest.TestCase):
    def test_board_first_deduped(self) -> None:
        with mock.patch.object(pricing, "load_aliases", return_value=["a/m", "b/m"]), \
                mock.patch.object(
                    pricing, "latest_run_candidates", return_value=["b/m", "c/m", "d/m"]
                ):
            routes, cand = pricing.snapshot_routes()
        self.assertEqual(routes, ["a/m", "b/m", "c/m", "d/m"])
        self.assertEqual(cand, ["c/m", "d/m"])


class WriteOutputsTests(unittest.TestCase):
    def test_writes_latest_and_identical_dated_copy(self) -> None:
        payload = {
            "generated_at": "t",
            "days": [{"date": "2026-08-27", "cost_usdc": "0.001000", "requests": 1}],
            "routes": {},
        }
        with tempfile.TemporaryDirectory() as tmp:
            latest, dated = pricing.write_outputs(payload, root=Path(tmp))
            self.assertEqual(latest, Path(tmp) / "data" / "pricing.json")
            self.assertTrue(latest.is_file())
            self.assertTrue(dated.is_file())
            self.assertEqual(dated.parent, Path(tmp) / "data" / "pricing")
            self.assertEqual(json.loads(latest.read_text()), payload)
            self.assertEqual(json.loads(dated.read_text()), json.loads(latest.read_text()))

    def test_same_day_rerun_overwrites_dated_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, dated1 = pricing.write_outputs({"generated_at": "t1"}, root=Path(tmp))
            _, dated2 = pricing.write_outputs({"generated_at": "t2"}, root=Path(tmp))
            self.assertEqual(dated1, dated2)  # same UTC day -> same file
            self.assertEqual(json.loads(dated2.read_text())["generated_at"], "t2")
            copies = list((Path(tmp) / "data" / "pricing").glob("*.json"))
            self.assertEqual(len(copies), 1)


if __name__ == "__main__":
    unittest.main()
