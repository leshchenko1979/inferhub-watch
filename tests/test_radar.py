from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "site"))

from probe import radar  # noqa: E402

import generate  # noqa: E402

ALIASES = ["ali/qwen3.8-max"]
PRICING_ROUTES = {
    # incumbent bar 0.005 $/M, no cache discount, 75/25 token mix
    "ali/qwen3.8-max": {
        "eff_per_mtok": 0.005,
        "cache_pct": 0.0,
        "tok_in": 750,
        "tok_out": 250,
    },
    # challenger: predicted = 0.001*1.0*0.75 + 0.001*0.25 = 0.001 -> 80% under
    "cb/qwen3.8-max": {"ask_in": 0.001, "ask_out": 0.001},
}
REGISTRY = [
    {"id": "core", "scores_rank": True},
    {"id": "cache", "scores_rank": True},
]


def make_run(candidate_statuses: dict[str, str], with_candidate: bool = True) -> dict:
    cells = [{"alias": "ali/qwen3.8-max", "check_id": "core", "status": "pass"}]
    if with_candidate:
        for check_id, status in candidate_statuses.items():
            cells.append(
                {
                    "alias": "cb/qwen3.8-max",
                    "check_id": check_id,
                    "status": status,
                    "candidate": True,
                    "model": "qwen3.8-max",
                }
            )
    return {"checks": ["core", "cache"], "cells": cells}


RUN_PASS = make_run({"core": "pass", "cache": "pass"})
RUN_FAIL = make_run({"core": "pass", "cache": "fail"})
RUN_BOARD_ONLY = make_run({}, with_candidate=False)


class VerdictTests(unittest.TestCase):
    def test_passing_challenger_margin(self) -> None:
        verdicts = radar.family_verdicts(RUN_PASS, PRICING_ROUTES, ALIASES)
        self.assertEqual(len(verdicts), 1)
        v = verdicts[0]
        self.assertEqual(v["family"], "qwen3.8-max")
        self.assertEqual(v["incumbent"], "ali/qwen3.8-max")
        self.assertAlmostEqual(v["incumbent_usd_m"], 0.005)
        self.assertEqual(v["challenger"], "cb/qwen3.8-max")
        self.assertAlmostEqual(v["challenger_usd_m"], 0.001)
        self.assertAlmostEqual(v["margin_pct"], 80.0)

    def test_failing_challenger_is_no_challenger(self) -> None:
        (v,) = radar.family_verdicts(RUN_FAIL, PRICING_ROUTES, ALIASES)
        self.assertIsNone(v["challenger"])
        self.assertIsNone(v["margin_pct"])

    def test_unbilled_incumbent_has_no_bar(self) -> None:
        routes = {"ali/qwen3.8-max": {"eff_per_mtok": None}}
        self.assertEqual(radar.family_verdicts(RUN_PASS, routes, ALIASES), [])

    def test_challenger_without_billed_asks_is_skipped(self) -> None:
        routes = {"ali/qwen3.8-max": PRICING_ROUTES["ali/qwen3.8-max"]}
        (v,) = radar.family_verdicts(RUN_PASS, routes, ALIASES)
        self.assertIsNone(v["challenger"])

    def test_measured_cache_beats_family_rate(self) -> None:
        run = make_run({"core": "pass", "cache": "pass"})
        for cell in run["cells"]:
            if cell.get("candidate") and cell["check_id"] == "cache":
                cell["evidence"] = {
                    "cached_tokens": 900,
                    "usage": {"prompt_tokens": 1000},
                }
        routes = dict(PRICING_ROUTES)
        routes["ali/qwen3.8-max"] = {**routes["ali/qwen3.8-max"], "cache_pct": 0.0}
        (v,) = radar.family_verdicts(run, routes, ALIASES)
        # measured 90% cache: 0.001 * 0.1 * 0.75 + 0.001 * 0.25 = 0.000325
        self.assertAlmostEqual(v["challenger_usd_m"], 0.000325)


class AlertLedgerTests(unittest.TestCase):
    def _verdict(self, margin):
        return {
            "family": "qwen3.8-max",
            "incumbent": "ali/qwen3.8-max",
            "incumbent_usd_m": 0.005,
            "challenger": "cb/qwen3.8-max",
            "challenger_usd_m": 0.001,
            "margin_pct": margin,
        }

    def test_new_margin_is_due(self) -> None:
        self.assertEqual(
            radar.due_alerts([self._verdict(80.0)], {}), [self._verdict(80.0)]
        )

    def test_same_margin_is_not_due(self) -> None:
        self.assertEqual(
            radar.due_alerts([self._verdict(80.0)], {"cb/qwen3.8-max": 80.0}), []
        )

    def test_widened_margin_is_due(self) -> None:
        self.assertEqual(
            radar.due_alerts([self._verdict(90.0)], {"cb/qwen3.8-max": 80.0}),
            [self._verdict(90.0)],
        )

    def test_below_threshold_is_never_due(self) -> None:
        self.assertEqual(
            radar.due_alerts([self._verdict(10.0)], {}), []
        )

    def test_no_challenger_is_never_due(self) -> None:
        v = self._verdict(None)
        self.assertEqual(radar.due_alerts([v], {}), [])

    def test_ledger_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            radar.save_ledger({"cb/qwen3.8-max": 80.0}, root)
            self.assertEqual(radar.load_ledger(root), {"cb/qwen3.8-max": 80.0})

    def test_malformed_ledger_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / radar.LEDGER_NAME).write_text("{broken")
            self.assertEqual(radar.load_ledger(root), {})


class PriceChipTests(unittest.TestCase):
    def _section(self, run, routes=None, aliases=None):
        return generate.probe_results_section(
            [run],
            aliases or ALIASES,
            REGISTRY,
            {"routes": routes if routes is not None else PRICING_ROUTES},
        )

    def test_cheaper_passing_challenger_shows_bad_chip(self) -> None:
        section = self._section(RUN_PASS)
        self.assertIn("chip price bad", section)
        self.assertIn("&#8722;80%", section)

    def test_no_challenger_shows_ok_chip(self) -> None:
        section = self._section(RUN_FAIL)
        self.assertIn("chip price ok", section)
        self.assertNotIn("chip price bad", section)

    def test_unbilled_incumbent_renders_without_price_chip(self) -> None:
        section = self._section(RUN_PASS, routes={})
        self.assertIn("model-group", section)
        self.assertNotIn("chip price", section)

    def test_old_suite_run_without_candidates_is_empty(self) -> None:
        self.assertEqual(self._section(RUN_BOARD_ONLY), "")

    def test_chip_html_without_verdict_is_empty(self) -> None:
        self.assertEqual(generate._price_chip_html(None), "")
        self.assertEqual(
            generate._price_chip_html({"incumbent_usd_m": None}), ""
        )


if __name__ == "__main__":
    unittest.main()
