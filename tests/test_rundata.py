from __future__ import annotations

import tempfile
import unittest

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "site"))

import rundata  # noqa: E402


class RundataBoardTests(unittest.TestCase):
    def test_scoring_short_and_rule(self) -> None:
        self.assertEqual(rundata.scoring_short("stream_tools"), "tools")
        self.assertEqual(rundata.scoring_short("cache_tools"), "cache")
        self.assertEqual(rundata.scoring_short("ru_mojibake"), "mojibake")
        self.assertEqual(
            rundata.scoring_rule(["stream_tools", "cache_tools", "ru_mojibake"]),
            "3/3: tools + cache + mojibake",
        )
        self.assertEqual(
            rundata.scoring_rule(["stream_tools", "cache_tools"]),
            "2/2: tools + cache",
        )

    def test_aliases_safe_first_keeps_relative_order(self) -> None:
        run = {
            "cells": [
                {"alias": "a", "check_id": "stream_tools", "status": "fail"},
                {"alias": "a", "check_id": "cache_tools", "status": "fail"},
                {"alias": "b", "check_id": "stream_tools", "status": "pass"},
                {"alias": "b", "check_id": "cache_tools", "status": "pass"},
                {"alias": "c", "check_id": "stream_tools", "status": "pass"},
                {"alias": "c", "check_id": "cache_tools", "status": "fail"},
            ]
        }
        ids = ["stream_tools", "cache_tools"]
        self.assertEqual(
            rundata.aliases_safe_first(["a", "b", "c"], run, ids),
            ["b", "a", "c"],
        )
        self.assertEqual(rundata.scoring_failed_ids(run, "c", ids), ["cache_tools"])

    def test_origin_gloss(self) -> None:
        self.assertEqual(
            rundata.origin_label({"origin": "github-actions"}), "Actions · CI"
        )
        self.assertEqual(rundata.origin_label({"origin": "local-seed"}), "seed · fixture")

    def test_display_specs_put_scoring_first(self) -> None:
        registry = [
            {"id": "usage_pricing", "scores_rank": False},
            {"id": "stream_tools", "scores_rank": True},
        ]
        self.assertEqual(
            [s["id"] for s in rundata.display_specs(registry)],
            ["stream_tools", "usage_pricing"],
        )


class PricingDataTests(unittest.TestCase):
    def test_rate_label_formats_compact(self) -> None:
        self.assertEqual(rundata.rate_label(0.014), "$0.014")
        self.assertEqual(rundata.rate_label("2.49"), "$2.49")
        self.assertEqual(rundata.rate_label(0.83), "$0.83")
        self.assertEqual(rundata.rate_label(0.00005), "$0.00005")
        self.assertEqual(rundata.rate_label(None), "")
        self.assertEqual(rundata.rate_label(0), "")
        self.assertEqual(rundata.rate_label("junk"), "")

    def test_token_and_cache_labels(self) -> None:
        self.assertEqual(rundata.token_label(999), "999")
        self.assertEqual(rundata.token_label(41_200_000), "41.2M")
        self.assertEqual(rundata.token_label(12_300), "12k")
        self.assertEqual(rundata.cache_label(68.7), "69%")
        self.assertEqual(rundata.cache_label(None), "")

    def test_pricing_rows_keeps_only_routes_with_rates(self) -> None:
        payload = {
            "routes": {
                "a": {"ask_in": 0.014, "eff_per_mtok": None},
                "b": {"ask_in": None, "eff_per_mtok": 0.02},
                "c": {"ask_in": None, "eff_per_mtok": None},
                "d": "junk",
            }
        }
        rows = rundata.pricing_rows(payload)
        self.assertEqual([r["route"] for r in rows], ["a", "b"])
        self.assertEqual(rundata.pricing_rows(None), [])
        self.assertEqual(rundata.pricing_rows({}), [])

    def test_load_pricing_reads_or_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertIsNone(rundata.load_pricing(root))
            (root / "data").mkdir()
            (root / "data" / "pricing.json").write_text('{"routes": {"a": {}}}')
            self.assertEqual(rundata.load_pricing(root)["routes"], {"a": {}})
            (root / "data" / "pricing.json").write_text("not json")
            self.assertIsNone(rundata.load_pricing(root))
            (root / "data" / "pricing.json").write_text('{"nope": 1}')
            self.assertIsNone(rundata.load_pricing(root))

    def test_log_bar_pct_scales_and_clamps(self) -> None:
        self.assertAlmostEqual(rundata.log_bar_pct(0.1, 0.001, 10.0), 50.0)
        self.assertEqual(rundata.log_bar_pct(0.001, 0.001, 10.0), 0.0)
        self.assertEqual(rundata.log_bar_pct(10.0, 0.001, 10.0), 100.0)
        self.assertEqual(rundata.log_bar_pct(99.0, 0.001, 10.0), 100.0)  # clamp
        self.assertEqual(rundata.log_bar_pct(0.0001, 0.001, 10.0), 0.0)  # clamp
        self.assertEqual(rundata.log_bar_pct(0, 0.001, 10.0), 0.0)
        self.assertEqual(rundata.log_bar_pct(None, 0.001, 10.0), 0.0)
        # single non-zero peer: its own bar still draws
        self.assertEqual(rundata.log_bar_pct(5, 5.0, 5.0), 100.0)
        self.assertEqual(rundata.log_bar_pct(5, 0.0, 0.0), 0.0)

    def test_peer_bounds_ignores_zeros(self) -> None:
        self.assertEqual(rundata.peer_bounds([0, 3.0, 7, 0.0]), (3.0, 7.0))
        self.assertEqual(rundata.peer_bounds([0, 0]), (0.0, 0.0))
        self.assertEqual(rundata.peer_bounds([]), (0.0, 0.0))

    def test_rate_color_class_thresholds(self) -> None:
        self.assertEqual(rundata.rate_color_class(0.0055), "ok")
        self.assertEqual(rundata.rate_color_class(0.02), "ok")
        self.assertEqual(rundata.rate_color_class(0.0596), "mid")
        self.assertEqual(rundata.rate_color_class(0.2), "mid")
        self.assertEqual(rundata.rate_color_class(1.3), "bad")
        self.assertEqual(rundata.rate_color_class(None), "")

    def test_cache_visuals(self) -> None:
        self.assertEqual(rundata.cache_color_class(91.3), "ok")
        self.assertEqual(rundata.cache_color_class(70), "ok")
        self.assertEqual(rundata.cache_color_class(53.1), "mid")
        self.assertEqual(rundata.cache_color_class(40), "mid")
        self.assertEqual(rundata.cache_color_class(0.0), "bad")
        self.assertEqual(rundata.cache_color_class(None), "")
        self.assertEqual(rundata.cache_bar_pct(61.4), 61.4)
        self.assertEqual(rundata.cache_bar_pct(150.0), 100.0)
        self.assertEqual(rundata.cache_bar_pct(None), 0.0)


if __name__ == "__main__":
    unittest.main()
