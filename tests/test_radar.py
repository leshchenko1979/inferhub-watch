from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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

    def test_verdict_carries_incumbent_reqs(self) -> None:
        routes = dict(PRICING_ROUTES)
        routes["ali/qwen3.8-max"] = {**routes["ali/qwen3.8-max"], "reqs": 321}
        (v,) = radar.family_verdicts(RUN_PASS, routes, ALIASES)
        self.assertEqual(v["incumbent_reqs"], 321)
        self.assertEqual(v["challenger_cache_source"], "family")

    def test_verdict_measured_cache_provenance(self) -> None:
        run = make_run({"core": "pass", "cache": "pass"})
        for cell in run["cells"]:
            if cell.get("candidate") and cell["check_id"] == "cache":
                cell["evidence"] = {
                    "cached_tokens": 900,
                    "usage": {"prompt_tokens": 1000},
                }
        (v,) = radar.family_verdicts(run, PRICING_ROUTES, ALIASES)
        self.assertEqual(v["challenger_cache_source"], "probe")
        self.assertAlmostEqual(v["challenger_cache_pct"], 90.0)


class DatedOnlyVerdictTests(unittest.TestCase):
    """A plain tail of a dated family is never a challenger."""

    INCUMBENT = {"eff_per_mtok": 0.009, "cache_pct": 0.0, "tok_in": 900, "tok_out": 100}

    def _run(self, candidate_alias: str) -> dict:
        cells = [{"alias": "ali/deepseek-v4-flash-0731", "check_id": "core", "status": "pass"}]
        for cid in ("core", "cache"):
            cells.append({
                "alias": candidate_alias, "check_id": cid, "status": "pass",
                "candidate": True, "model": "deepseek-v4-flash",
            })
        return {"checks": ["core", "cache"], "cells": cells}

    def test_plain_challenger_is_rejected(self) -> None:
        routes = {
            "ali/deepseek-v4-flash-0731": self.INCUMBENT,
            # plain flash undercuts the bar hard — still never a challenger
            "ocg/deepseek-v4-flash": {"ask_in": 0.0001, "ask_out": 0.0003},
        }
        (v,) = radar.family_verdicts(
            self._run("ocg/deepseek-v4-flash"), routes, ["ali/deepseek-v4-flash-0731"]
        )
        self.assertEqual(v["family"], "deepseek-v4-flash")
        self.assertIsNone(v["challenger"])
        self.assertIsNone(v["margin_pct"])

    def test_dated_challenger_still_lands(self) -> None:
        routes = {
            "ali/deepseek-v4-flash-0731": self.INCUMBENT,
            # predicted 0.005*0.9 + 0.015*0.1 = 0.006 < 0.009
            "cb/deepseek-v4-flash-0731": {"ask_in": 0.005, "ask_out": 0.015},
        }
        (v,) = radar.family_verdicts(
            self._run("cb/deepseek-v4-flash-0731"), routes, ["ali/deepseek-v4-flash-0731"]
        )
        self.assertEqual(v["challenger"], "cb/deepseek-v4-flash-0731")
        self.assertAlmostEqual(v["challenger_usd_m"], 0.006)


class FamilyIsolationTests(unittest.TestCase):
    def test_poisoned_family_is_skipped_not_fatal(self) -> None:
        # the glm incumbent carries a non-numeric cache_pct: its family is
        # skipped with a note, the healthy qwen verdict still lands
        routes = {
            "ali/qwen3.8-max": PRICING_ROUTES["ali/qwen3.8-max"],
            "cb/qwen3.8-max": PRICING_ROUTES["cb/qwen3.8-max"],
            "zai/glm-5.3": {"eff_per_mtok": 0.02, "cache_pct": "garbage",
                            "tok_in": 750, "tok_out": 250},
        }
        buf = io.StringIO()
        with mock.patch.object(sys, "stderr", buf):
            verdicts = radar.family_verdicts(RUN_PASS, routes, ALIASES + ["zai/glm-5.3"])
        self.assertEqual([v["family"] for v in verdicts], ["qwen3.8-max"])
        self.assertIn("glm-5.3 skipped", buf.getvalue())

    def test_unbilled_family_stays_silent(self) -> None:
        # no billed incumbent is a normal skip — no stderr noise
        routes = {"ali/qwen3.8-max": {"eff_per_mtok": None}}
        buf = io.StringIO()
        with mock.patch.object(sys, "stderr", buf):
            self.assertEqual(radar.family_verdicts(RUN_PASS, routes, ALIASES), [])
        self.assertEqual(buf.getvalue(), "")

    def test_estimate_cache_verdict_never_alerts(self) -> None:
        # 80% margin but challenger priced on the family cache estimate
        (v,) = radar.family_verdicts(RUN_PASS, PRICING_ROUTES, ALIASES)
        self.assertEqual(v["challenger_cache_source"], "family")
        self.assertAlmostEqual(v["margin_pct"], 80.0)
        self.assertEqual(radar.due_alerts([v], {}), [])

    def test_measured_cache_verdict_alerts(self) -> None:
        # same 80% margin with probe-measured cache evidence is due
        run = make_run({"core": "pass", "cache": "pass"})
        for cell in run["cells"]:
            if cell.get("candidate") and cell["check_id"] == "cache":
                cell["evidence"] = {"cached_tokens": 0, "usage": {"prompt_tokens": 1000}}
        (v,) = radar.family_verdicts(run, PRICING_ROUTES, ALIASES)
        self.assertEqual(v["challenger_cache_source"], "probe")
        self.assertEqual(len(radar.due_alerts([v], {})), 1)


class AlertLedgerTests(unittest.TestCase):
    def _verdict(self, margin):
        return {
            "family": "qwen3.8-max",
            "incumbent": "ali/qwen3.8-max",
            "incumbent_usd_m": 0.005,
            "challenger": "cb/qwen3.8-max",
            "challenger_usd_m": 0.001,
            "challenger_cache_source": "probe",
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

    def test_board_only_run_renders_board_group(self) -> None:
        # Suite v2 renders every probed family — a board-only run (no
        # shortlist candidates) still shows its incumbent group + verdict.
        section = self._section(RUN_BOARD_ONLY)
        self.assertIn('class="model-group"', section)
        self.assertIn("qwen3.8-max", section)
        self.assertIn("ali/qwen3.8-max", section)

    def test_probe_only_bar_carries_star(self) -> None:
        chip = generate._price_chip_html(
            {
                "incumbent": "ali/deepseek-v4-flash-0731",
                "incumbent_usd_m": 0.0085,
                "incumbent_reqs": 2,
                "challenger": "ocg/deepseek-v4-flash",
                "challenger_usd_m": 0.0004,
                "challenger_cache_pct": 98.0,
                "challenger_cache_source": "probe",
                "margin_pct": 95.0,
            }
        )
        self.assertIn("/M* ·", chip)
        self.assertIn("probe-only sample (2 requests)", chip)
        self.assertIn("98% measured probe cache", chip)

    def test_real_traffic_bar_carries_no_star(self) -> None:
        chip = generate._price_chip_html(
            {
                "incumbent": "ali/qwen3.8-max",
                "incumbent_usd_m": 0.005,
                "incumbent_reqs": 500,
                "challenger": None,
                "challenger_usd_m": None,
                "challenger_cache_pct": None,
                "challenger_cache_source": None,
                "margin_pct": None,
            }
        )
        self.assertIn("/M ·", chip)
        self.assertNotIn("*", chip)
        self.assertIn("billed across 500 requests", chip)

    def test_chip_html_without_verdict_is_empty(self) -> None:
        self.assertEqual(generate._price_chip_html(None), "")
        self.assertEqual(
            generate._price_chip_html({"incumbent_usd_m": None}), ""
        )


NOTIFY_VERDICT = {
    "family": "qwen3.8-max",
    "incumbent": "ali/qwen3.8-max",
    "incumbent_usd_m": 0.005,
    "challenger": "cb/qwen3.8-max",
    "challenger_usd_m": 0.001,
    "margin_pct": 80.0,
}


class NotifyTests(unittest.TestCase):
    def test_no_session_env_builds_no_command(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(radar.notify_command([NOTIFY_VERDICT]))

    def test_no_due_alerts_builds_no_command(self) -> None:
        env = {radar.NOTIFY_SESSION_ENV: "abc-123"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertIsNone(radar.notify_command([]))

    def test_session_env_builds_notify_command(self) -> None:
        env = {radar.NOTIFY_SESSION_ENV: "abc-123"}
        with mock.patch.dict(os.environ, env, clear=True):
            cmd = radar.notify_command([NOTIFY_VERDICT])
        self.assertIsNotNone(cmd)
        self.assertEqual(cmd[0], "opencrabs")
        self.assertNotIn("-p", cmd)  # profile opt-in, not hardcoded
        self.assertEqual(cmd[-1], "abc-123")
        text = cmd[cmd.index("--text") + 1]
        self.assertIn("qwen3.8-max", text)
        self.assertIn("cb/qwen3.8-max", text)
        self.assertIn("80% cheaper", text)

    def test_profile_env_adds_profile_flag(self) -> None:
        env = {radar.NOTIFY_SESSION_ENV: "abc-123", radar.NOTIFY_PROFILE_ENV: "ops"}
        with mock.patch.dict(os.environ, env, clear=True):
            cmd = radar.notify_command([NOTIFY_VERDICT])
        self.assertEqual(cmd[1:3], ["-p", "ops"])

    def test_notify_alerts_calls_subprocess_once(self) -> None:
        env = {radar.NOTIFY_SESSION_ENV: "abc-123"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch.object(radar.subprocess, "run") as run:
                run.return_value = mock.Mock(returncode=0)
                radar.notify_alerts([NOTIFY_VERDICT])
        self.assertEqual(run.call_count, 1)

    def test_notify_alerts_swallows_errors(self) -> None:
        env = {radar.NOTIFY_SESSION_ENV: "abc-123"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch.object(radar.subprocess, "run", side_effect=OSError("boom")):
                radar.notify_alerts([NOTIFY_VERDICT])  # must not raise


if __name__ == "__main__":
    unittest.main()
