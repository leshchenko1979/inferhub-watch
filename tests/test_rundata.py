from __future__ import annotations

import json
import tempfile
import unittest

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "site"))

import rundata  # noqa: E402


class RundataBoardTests(unittest.TestCase):
    def test_scoring_short_and_rule(self) -> None:
        self.assertEqual(rundata.scoring_short("core"), "core")
        self.assertEqual(rundata.scoring_short("cache"), "cache")
        self.assertEqual(rundata.scoring_short("cache_tools"), "cache tools")
        self.assertEqual(
            rundata.scoring_rule(["core", "cache"]),
            "2/2: core + cache",
        )
        self.assertEqual(
            rundata.scoring_rule(["core"]),
            "1/1: core",
        )

    def test_alias_probed(self) -> None:
        run = {"cells": [{"alias": "a", "check_id": "x", "status": "pass"}]}
        self.assertTrue(rundata.alias_probed(run, "a"))
        self.assertFalse(rundata.alias_probed(run, "b"))
        self.assertFalse(rundata.alias_probed({"cells": []}, "a"))
        self.assertFalse(rundata.alias_probed({}, "a"))

    def test_candidate_cells_stay_out_of_board_views(self) -> None:
        run = {
            "cells": [
                {"alias": "ocg/deepseek-v4-flash", "check_id": "stream_tools", "status": "pass"},
                {"alias": "ocg/deepseek-v4-flash", "check_id": "cache_tools", "status": "pass", "cost_usdc": "0.0002"},
                {"alias": "cb/gpt-5.6-luna", "check_id": "stream_tools", "status": "pass", "candidate": True, "model": "gpt-5.6-luna"},
                {"alias": "cb/gpt-5.6-luna", "check_id": "cache_tools", "status": "fail", "candidate": True, "model": "gpt-5.6-luna", "cost_usdc": "0.0001"},
            ]
        }
        self.assertEqual(len(rundata.board_cells(run)), 2)
        self.assertEqual(len(rundata.candidate_cells(run)), 2)
        cmap = rundata.cell_map(run)
        self.assertNotIn(("cb/gpt-5.6-luna", "stream_tools"), cmap)
        self.assertIn(("ocg/deepseek-v4-flash", "stream_tools"), cmap)
        self.assertFalse(rundata.alias_probed(run, "cb/gpt-5.6-luna"))
        self.assertTrue(rundata.alias_probed(run, "ocg/deepseek-v4-flash"))
        self.assertEqual(
            rundata.scoring_pass_count(run, "cb/gpt-5.6-luna", ["stream_tools", "cache_tools"]),
            (0, 2),
        )
        # per-alias cost is a board view: candidates excluded
        self.assertEqual(rundata.alias_run_cost(run, "cb/gpt-5.6-luna"), "")
        self.assertEqual(rundata.alias_run_cost(run, "ocg/deepseek-v4-flash"), "$0.0002")
        # run total is the true bill: candidates included
        self.assertEqual(rundata.run_total_cost(run), "$0.0003")

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
        self.assertEqual(rundata.rate_color_class(1.3), "mid")
        self.assertEqual(rundata.rate_color_class(None), "")

    def test_cache_visuals(self) -> None:
        self.assertEqual(rundata.cache_color_class(91.3), "ok")
        self.assertEqual(rundata.cache_color_class(70), "ok")
        self.assertEqual(rundata.cache_color_class(53.1), "mid")
        self.assertEqual(rundata.cache_color_class(40), "mid")
        self.assertEqual(rundata.cache_color_class(0.0), "mid")
        self.assertEqual(rundata.cache_color_class(None), "")
        self.assertEqual(rundata.cache_bar_pct(61.4), 61.4)
        self.assertEqual(rundata.cache_bar_pct(150.0), 100.0)
        self.assertEqual(rundata.cache_bar_pct(None), 0.0)


class SpendDashboardTests(unittest.TestCase):
    def _write_dated(self, root: Path, day: str, payload: dict | str) -> None:
        directory = root / "data" / "pricing"
        directory.mkdir(parents=True, exist_ok=True)
        text = payload if isinstance(payload, str) else json.dumps(payload)
        (directory / f"{day}.json").write_text(text)

    def test_load_dated_pricing_orders_and_skips_unusable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(rundata.load_dated_pricing(root), [])
            self._write_dated(root, "2026-08-26", {"routes": {"a": {}}})
            self._write_dated(root, "2026-08-27", {"routes": {}})
            self._write_dated(root, "2026-08-25", "not json")
            self._write_dated(root, "2026-08-24", {"nope": 1})
            dated = rundata.load_dated_pricing(root)
            self.assertEqual([day for day, _ in dated], ["2026-08-26", "2026-08-27"])

    def test_prior_pricing_needs_strictly_earlier_day(self) -> None:
        old = {"generated_at": "2026-08-26T06:00:00+00:00", "routes": {}}
        new = {"generated_at": "2026-08-27T06:00:00+00:00", "routes": {}}
        current = {"generated_at": "2026-08-27T06:00:00+00:00"}
        dated = [("2026-08-26", old), ("2026-08-27", new)]
        self.assertIs(rundata.prior_pricing(dated, current), old)
        self.assertIsNone(rundata.prior_pricing([("2026-08-27", new)], current))
        self.assertIsNone(rundata.prior_pricing([], current))

    def test_spend_days_validates_entries(self) -> None:
        good = {"date": "2026-08-27", "cost_usdc": "0.5", "requests": 2}
        payload = {"days": [good, {"nope": 1}, "junk"]}
        self.assertEqual(rundata.spend_days(payload), [good])
        self.assertEqual(rundata.spend_days(None), [])
        self.assertEqual(rundata.spend_days({"days": "junk"}), [])

    def test_spend_between_sums_inclusive_range(self) -> None:
        days = [
            {"date": "2026-07-31", "cost_usdc": "1.0"},
            {"date": "2026-08-01", "cost_usdc": "2.0"},
            {"date": "2026-08-27", "cost_usdc": "0.5"},
            {"date": "2026-08-27", "cost_usdc": "junk"},
        ]
        self.assertEqual(rundata.spend_between(days, "2026-08-01", "2026-08-27"), 2.5)
        self.assertEqual(rundata.spend_between(days, "2026-08-27", "2026-08-27"), 0.5)

    def test_probe_spend_counts_all_cells(self) -> None:
        runs = [
            {"cells": [
                {"cost_usdc": "0.001"},
                {"cost_usdc": ""},
                {"candidate": True, "cost_usdc": "0.004"},
            ]},
            {"cells": [{"cost_usdc": "junk"}]},
        ]
        self.assertAlmostEqual(rundata.probe_spend(runs), 0.005)
        self.assertEqual(rundata.probe_spend([]), 0.0)

    def test_ask_deltas_none_without_prior(self) -> None:
        current = {"routes": {"a/m": {"ask_in": 0.4, "ask_out": 1.5}}}
        self.assertIsNone(rundata.ask_deltas(current, None, "a/m"))

    def test_ask_deltas_values_and_gaps(self) -> None:
        prior = {"routes": {
            "a/m": {"ask_in": 0.5, "ask_out": 1.5},
            "b/m": {"ask_in": None, "ask_out": None},
        }}
        current = {"routes": {
            "a/m": {"ask_in": 0.4, "ask_out": 1.5},
            "b/m": {"ask_in": 1.0, "ask_out": 3.0},
            "new/m": {"ask_in": 1.0, "ask_out": 3.0},
        }}
        deltas = rundata.ask_deltas(current, prior, "a/m")
        self.assertAlmostEqual(deltas["in"], -0.1)
        self.assertEqual(deltas["out"], 0.0)
        self.assertIsNone(rundata.ask_deltas(current, prior, "new/m"))  # new route
        self.assertIsNone(rundata.ask_deltas(current, prior, "b/m"))   # prior unrated
        self.assertIsNone(rundata.ask_deltas(current, prior, "ghost")) # absent both

    def test_month_day_label(self) -> None:
        self.assertEqual(rundata.month_day_label("2026-08-21"), "Aug 21")
        self.assertEqual(rundata.month_day_label("2026-01-05"), "Jan 5")
        self.assertEqual(rundata.month_day_label("junk"), "")
        self.assertEqual(rundata.month_day_label("2026-13-40"), "")


class CandidatesHelpersTests(unittest.TestCase):
    SCORE = ["stream_tools", "cache_tools", "ru_mojibake"]

    def _cell(self, alias, cid, status="pass", candidate=True, evidence=None):
        cell = {
            "alias": alias,
            "check_id": cid,
            "status": status,
            "resolved_model": alias,
        }
        if candidate:
            cell["candidate"] = True
        if evidence is not None:
            cell["evidence"] = evidence
        return cell

    def test_pricing_rows_skip_candidate_entries(self) -> None:
        payload = {"routes": {
            "a/board": {"ask_in": 1.0, "ask_out": 3.0},
            "c/cand": {"ask_in": 1.0, "ask_out": 3.0, "candidate": True},
        }}
        self.assertEqual([r["route"] for r in rundata.pricing_rows(payload)], ["a/board"])

    def test_candidate_pass_count_and_failed_ids(self) -> None:
        run = {"cells":
            [self._cell("c/m", "stream_tools"),
             self._cell("c/m", "cache_tools", status="fail"),
             self._cell("c/m", "ru_mojibake")]
        }
        self.assertEqual(rundata.candidate_pass_count(run, "c/m", self.SCORE), (2, 3))
        self.assertEqual(rundata.candidate_failed_ids(run, "c/m", self.SCORE), ["cache_tools"])
        self.assertEqual(rundata.candidate_pass_count(run, "other", self.SCORE), (0, 3))

    def test_candidate_cache_pct(self) -> None:
        run = {"cells": [self._cell("c/m", "cache", evidence={
            "cached_tokens": 930, "usage": {"prompt_tokens": 1000}})]}
        self.assertEqual(rundata.candidate_cache_pct(run, "c/m"), 93.0)
        self.assertIsNone(rundata.candidate_cache_pct(run, "ghost"))
        empty = {"cells": [self._cell("c/m", "cache", evidence={
            "cached_tokens": 0, "usage": {"prompt_tokens": 1000}})]}
        self.assertEqual(rundata.candidate_cache_pct(empty, "c/m"), 0.0)

    def test_route_window_record_counts_runs(self) -> None:
        good = {"cells": [self._cell("c/m", cid) for cid in self.SCORE]}
        bad = {"cells": [self._cell("c/m", cid, status="fail") for cid in self.SCORE]}
        other = {"cells": [self._cell("x/y", cid) for cid in self.SCORE]}
        runs = [good, bad, other, good]
        self.assertEqual(
            rundata.route_window_record(runs, "c/m", self.SCORE, candidate=True), (2, 3)
        )
        # board track: same cells but untagged
        board_runs = [
            {"cells": [{**c, "candidate": False} for c in good["cells"]]},
            {"cells": [{**c, "candidate": False} for c in bad["cells"]]},
        ]
        self.assertEqual(
            rundata.route_window_record(board_runs, "c/m", self.SCORE, candidate=False), (1, 2)
        )

    def test_incumbent_aliases_family_match(self) -> None:
        aliases = ["ali/qwen3.8-max", "cp/cline-pass/deepseek-v4-pro",
                   "cmc/deepseek/deepseek-v4-pro", "zai/glm-5.3-flash",
                   "ali/deepseek-v4-flash-0731"]
        self.assertEqual(
            rundata.incumbent_aliases(aliases, "deepseek-v4-pro"),
            ["cp/cline-pass/deepseek-v4-pro", "cmc/deepseek/deepseek-v4-pro"],
        )
        self.assertEqual(rundata.incumbent_aliases(aliases, "qwen3.8-max"), ["ali/qwen3.8-max"])
        # glm-5.3-flash keeps its own family — never grouped with glm-5.3
        self.assertEqual(
            rundata.incumbent_aliases(aliases, "glm-5.3-flash"), ["zai/glm-5.3-flash"]
        )
        self.assertEqual(rundata.incumbent_aliases(aliases, "glm-5.3"), [])
        self.assertEqual(
            rundata.incumbent_aliases(aliases, "deepseek-v4-flash"),
            ["ali/deepseek-v4-flash-0731"],
        )
        self.assertEqual(rundata.incumbent_aliases(aliases, "gpt-5.6-luna"), [])


if __name__ == "__main__":
    unittest.main()
