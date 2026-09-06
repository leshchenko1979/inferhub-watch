"""Tests for probe.daily_report — offline: synthetic rows, tmp snapshots."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from probe import daily_report


def _row(model="ali/qwen3.8-max", tok_in=1000, tok_out=2000, cached=0,
         cost=0.05, status="ok", ts="2026-09-04T10:00:00Z"):
    return {
        "model": model,
        "ts": ts,
        "status": status,
        "prompt_tokens": tok_in,
        "completion_tokens": tok_out,
        "cached_tokens": cached,
        "cost_consumer_usdc": str(cost),
    }


def _snap(root: Path, day: str, routes: dict) -> None:
    payload = {
        "generated_at": f"{day}T06:15:00+00:00",
        "range": "30d",
        "requests_scanned": 10,
        "days": [{"date": day, "cost_usdc": "0.500000", "requests": 7}],
        "routes": routes,
    }
    (root / "data" / "pricing").mkdir(parents=True, exist_ok=True)
    (root / "data" / "pricing" / f"{day}.json").write_text(json.dumps(payload))


def _entry(ask_in, ask_out):
    return {"ask_in": ask_in, "ask_out": ask_out}


class Usage24hTest(unittest.TestCase):
    def test_aggregates_traffic_cache_and_cost(self):
        rows = [
            _row(tok_in=1000, tok_out=2000, cached=400, cost=0.10),
            _row(tok_in=100, tok_out=200, cached=0, cost=0.02),
        ]
        stats = daily_report.usage_24h(rows)
        agg = stats["ali/qwen3.8-max"]
        self.assertEqual(agg["reqs"], 2)
        self.assertEqual(agg["tok_in"], 1100)
        self.assertEqual(agg["tok_out"], 2200)
        self.assertEqual(agg["cached"], 400)
        self.assertAlmostEqual(agg["cost"], 0.12)

    def test_failed_rows_count_but_carry_no_tokens(self):
        rows = [
            _row(cost=0.10),
            _row(status="failed", tok_in=0, tok_out=0, cost=0),
        ]
        stats = daily_report.usage_24h(rows)
        agg = stats["ali/qwen3.8-max"]
        self.assertEqual(agg["reqs"], 2)
        self.assertEqual(agg["failed"], 1)
        self.assertEqual(agg["tok_in"], 1000)
        self.assertAlmostEqual(agg["cost"], 0.10)

    def test_rows_without_model_are_skipped(self):
        self.assertEqual(daily_report.usage_24h([_row(model="")]), {})


class PriceMovementsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_flags_real_move_and_ignores_noise_and_new_route(self):
        _snap(self.root, "2026-09-03", {
            "ali/kimi-k3": _entry(7.0, 21.0),
            "ali/qwen3.8-max": _entry(2.0, 8.0),
        })
        _snap(self.root, "2026-09-04", {
            "ali/kimi-k3": _entry(5.0, 21.0),
            "ali/qwen3.8-max": _entry(2.0 + 1e-12, 8.0),
            "cx/gpt-5.6-luna": _entry(1.0, 3.0),  # new route: not a move
        })
        moves = daily_report.price_movements(self.root)
        self.assertEqual(
            [(m["alias"], m["field"], m["was"], m["now"]) for m in moves],
            [("ali/kimi-k3", "ask_in", 7.0, 5.0)],
        )

    def test_needs_two_snapshots(self):
        _snap(self.root, "2026-09-04", {"ali/kimi-k3": _entry(7.0, 21.0)})
        self.assertEqual(daily_report.price_movements(self.root), [])

    def test_zero_to_priced_is_a_move(self):
        """A3 (review 2026-09-04): was_v=0 made rel=0.0 and the gate hid a
        genuine 0 -> price introduction forever."""
        _snap(self.root, "2026-09-03", {
            "ali/kimi-k3": _entry(0.0, 21.0),      # unpriced in, now priced
            "ali/qwen3.8-max": _entry(2.0, 0.0),   # priced in, unpriced out
        })
        _snap(self.root, "2026-09-04", {
            "ali/kimi-k3": _entry(0.03, 21.0),
            "ali/qwen3.8-max": _entry(2.0, 0.48),
        })
        moves = daily_report.price_movements(self.root)
        self.assertEqual(
            [(m["alias"], m["field"], m["was"], m["now"], m["pct"]) for m in moves],
            [
                ("ali/kimi-k3", "ask_in", 0.0, 0.03, None),
                ("ali/qwen3.8-max", "ask_out", 0.0, 0.48, None),
            ],
        )
        # rendering marks it as a new price, never "+0.0%"
        lines = daily_report.movements_section(moves, self.root)
        self.assertIn("(new price)", "\n".join(lines))
        self.assertNotIn("+0.0%", "\n".join(lines))


class BuildReportTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        _snap(self.root, "2026-09-03", {"ali/kimi-k3": _entry(7.0, 21.0)})
        _snap(self.root, "2026-09-04", {"ali/kimi-k3": _entry(5.0, 21.0)})
        rows = [_row(cached=500, cost=0.10)]

    def test_report_has_all_sections_and_no_key(self):
        rows = [_row(cached=500, cost=0.10)]
        with mock.patch.object(
            daily_report, "fetch_log_rows", return_value=rows
        ):
            text = daily_report.build_report("SECRET-KEY", self.root)
        self.assertIn("Inferhub daily", text)
        self.assertIn("Last 24h", text)
        self.assertIn("ali/qwen3.8-max", text)
        self.assertIn("Spend by UTC day", text)
        self.assertIn("Ask moves", text)
        self.assertIn("ali/kimi-k3", text)
        self.assertIn("-28.6%", text)
        self.assertNotIn("SECRET-KEY", text)

    def test_empty_window_still_renders(self):
        # same asks on both days: no movements expected
        _snap(self.root, "2026-09-04", {"ali/kimi-k3": _entry(7.0, 21.0)})
        with mock.patch.object(daily_report, "fetch_log_rows", return_value=[]):
            text = daily_report.build_report("SECRET-KEY", self.root)
        self.assertIn("No traffic in the window.", text)
        self.assertIn("No ask moves", text)


class LoadKeyTest(unittest.TestCase):
    def test_env_wins_over_keys_file(self):
        with mock.patch.dict(
            os.environ, {"INFERHUB_API_KEY": "env-key"}, clear=False
        ):
            self.assertEqual(daily_report.load_key(), "env-key")

    def test_missing_everywhere_returns_empty(self):
        with mock.patch.dict(
            os.environ, {}, clear=True
        ), mock.patch.object(
            daily_report, "KEYS_FILE", Path("/nonexistent/keys.toml")
        ):
            self.assertEqual(daily_report.load_key(), "")


class SpendByDaySectionTest(unittest.TestCase):
    """spend_by_day_section — B2 gap: the spend table was never covered."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_no_snapshots_renders_nothing(self) -> None:
        self.assertEqual(daily_report.spend_by_day_section(self.root), [])

    def test_renders_days_with_zero_cost_as_dash(self) -> None:
        self.root.joinpath("data").mkdir()
        _snap(self.root, "2026-09-04", {})
        snap = json.loads(
            (self.root / "data" / "pricing" / "2026-09-04.json").read_text())
        snap["days"] = [
            {"date": "2026-09-02", "cost_usdc": "1.61", "requests": 2264},
            {"date": "2026-09-03", "cost_usdc": "0.0", "requests": 10},
            {"date": "2026-09-04", "cost_usdc": None, "requests": 3},
        ]
        (self.root / "data" / "pricing" / "2026-09-04.json").write_text(
            json.dumps(snap))
        lines = daily_report.spend_by_day_section(self.root)
        text = "\n".join(lines)
        self.assertIn("| 2026-09-02 | 2,264 | 1.61 |", text)
        self.assertIn("| 2026-09-03 | 10 | — |", text)
        self.assertIn("| 2026-09-04 | 3 | — |", text)

    def test_snapshot_without_days_renders_nothing(self) -> None:
        self.root.joinpath("data").mkdir()
        _snap(self.root, "2026-09-04", {})
        snap = json.loads(
            (self.root / "data" / "pricing" / "2026-09-04.json").read_text())
        snap["days"] = []
        (self.root / "data" / "pricing" / "2026-09-04.json").write_text(
            json.dumps(snap))
        self.assertEqual(daily_report.spend_by_day_section(self.root), [])


class BoardingCandidatesTest(unittest.TestCase):
    """Four-gate boarding flag — all gates must hold; missing data degrades.

    Fixture family: incumbent ali/qwen3.8-max on the board, candidate
    cb/qwen3.8-max cheaper, probed, billed, IQ-washed, fast enough.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    @staticmethod
    def _routes(entry_eff=0.003, inc_eff=0.0069):
        return {
            "cb/qwen3.8-max": {"ask_in": 0.016, "ask_out": 0.048,
                               "eff_per_mtok": entry_eff, "cache_pct": 70.0,
                               "reqs": 50, "tok_in": 100000, "tok_out": 2000,
                               "cost_usdc": "0.000300"},
            "ali/qwen3.8-max": {"ask_in": 0.016, "ask_out": 0.048,
                                "eff_per_mtok": inc_eff, "cache_pct": 69.6,
                                "reqs": 1900, "tok_in": 140000000,
                                "tok_out": 2700000, "cost_usdc": "1.000000"},
        }

    @staticmethod
    def _run():
        return {"cells": [{"alias": "cb/qwen3.8-max", "check_id": "core",
                           "status": "pass"}]}

    @staticmethod
    def _perf():
        return {"cb/qwen3.8-max": {"ttft_p50_ms": 2000.0},
                "ali/qwen3.8-max": {"ttft_p50_ms": 2263.0}}

    ALIASES = ["ali/qwen3.8-max"]

    def test_all_four_gates_met_flags_candidate(self):
        snaps = [self._routes() for _ in range(3)]
        out = daily_report.boarding_candidates(
            snaps, self._run(), self._perf(), self.ALIASES)
        self.assertEqual([c["route"] for c in out], ["cb/qwen3.8-max"])
        lines = daily_report.boarding_section(out)
        text = "\n".join(lines)
        self.assertIn("Boarding candidates", text)
        self.assertIn("cb/qwen3.8-max", text)

    def test_two_days_under_bar_is_not_enough(self):
        snaps = [self._routes(), self._routes(), self._routes(entry_eff=0.008)]
        out = daily_report.boarding_candidates(
            snaps, self._run(), self._perf(), self.ALIASES)
        self.assertEqual(out, [])

    def test_no_core_pass_never_flags(self):
        run = {"cells": [{"alias": "cb/qwen3.8-max", "check_id": "core",
                          "status": "fail"}]}
        out = daily_report.boarding_candidates(
            [self._routes() for _ in range(3)], run, self._perf(), self.ALIASES)
        self.assertEqual(out, [])
        # no probe record at all: gate (a) fails closed
        out = daily_report.boarding_candidates(
            [self._routes() for _ in range(3)], None, self._perf(), self.ALIASES)
        self.assertEqual(out, [])

    def test_board_routes_are_never_candidates(self):
        out = daily_report.boarding_candidates(
            [self._routes() for _ in range(3)], self._run(), self._perf(),
            self.ALIASES + ["cb/qwen3.8-max"])
        self.assertEqual(out, [])

    def test_iq_wash_failure_blocks(self):
        # incumbent IQ 50, candidate IQ 43 -> below the 0.9 wash floor
        out = daily_report.boarding_candidates(
            [self._routes() for _ in range(3)], self._run(), self._perf(),
            self.ALIASES, iq_fn=lambda r: 43.0 if "cb/" in r else 50.0)
        self.assertEqual(out, [])

    def test_iq_wash_passes_at_floor_boundary(self):
        # candidate IQ exactly 0.9x incumbent passes (>= is the rule)
        out = daily_report.boarding_candidates(
            [self._routes() for _ in range(3)], self._run(), self._perf(),
            self.ALIASES, iq_fn=lambda r: 45.0 if "cb/" in r else 50.0)
        self.assertEqual([c["route"] for c in out], ["cb/qwen3.8-max"])

    def test_ttft_failure_blocks(self):
        # candidate ttft 6s vs incumbent 2.26s = >2x BOARD_TTFT_MULTIPLE
        perf = {"cb/qwen3.8-max": {"ttft_p50_ms": 6000.0},
                "ali/qwen3.8-max": {"ttft_p50_ms": 2263.0}}
        out = daily_report.boarding_candidates(
            [self._routes() for _ in range(3)], self._run(), perf,
            self.ALIASES, iq_fn=lambda r: None)
        self.assertEqual(out, [])

    def test_empty_perf_passes_speed_gate(self):
        # unknown ttft degrades to a pass — route must earn on what is known
        out = daily_report.boarding_candidates(
            [self._routes() for _ in range(3)], self._run(), {},
            self.ALIASES, iq_fn=lambda r: None)
        self.assertEqual([c["route"] for c in out], ["cb/qwen3.8-max"])

    def test_candidate_family_without_board_incumbent_is_dropped(self):
        out = daily_report.boarding_candidates(
            [self._routes() for _ in range(3)], self._run(), self._perf(),
            aliases=["other-family/ incumbent"])  # no incumbent in cand's family
        self.assertEqual(out, [])

    def test_fewer_snapshots_than_board_snapshots_fails(self):
        out = daily_report.boarding_candidates(
            [self._routes() for _ in range(2)], self._run(), self._perf(),
            self.ALIASES, iq_fn=lambda r: None)
        self.assertEqual(out, [])

    def test_never_billed_route_is_skipped(self):
        routes = self._routes()
        routes["cb/qwen3.8-max"]["reqs"] = 0
        snaps = [routes for _ in range(3)]
        out = daily_report.boarding_candidates(
            snaps, self._run(), self._perf(), self.ALIASES)
        self.assertEqual(out, [])

    def test_build_report_renders_boarding_line(self):
        for day in ("2026-09-04", "2026-09-05", "2026-09-06"):
            _snap(self.root, day, self._routes())
        (self.root / "data" / "runs").mkdir(parents=True, exist_ok=True)
        (self.root / "data" / "runs" / "r.json").write_text(json.dumps(self._run()))
        perf_payload = {"perf": {"models": self._perf()}}
        (self.root / "data" / "pricing.json").write_text(json.dumps(perf_payload))
        snap = ("2026-09-06", {"routes": self._routes(),
                               "days": [{"date": "2026-09-06",
                                         "cost_usdc": "0.5",
                                         "requests": 7}]})
        with mock.patch.object(daily_report, "fetch_log_rows", return_value=[]), \
                mock.patch.object(daily_report, "_latest_snapshots",
                                  return_value=[snap] * 3), \
                mock.patch.object(daily_report, "price_movements",
                                  return_value=[]), \
                mock.patch("probe.market.load_aliases",
                           return_value=self.ALIASES), \
                mock.patch("probe.registry.load_aliases",
                           return_value=self.ALIASES):
            text = daily_report.build_report("SECRET-KEY", self.root)
        self.assertIn("Boarding candidates", text)
        self.assertIn("cb/qwen3.8-max", text)

class FormatterTest(unittest.TestCase):
    """B5 gap: _fmt_pct and _ask's bare-prefix edges were untested."""

    def test_fmt_pct_branches(self) -> None:
        self.assertEqual(daily_report._fmt_pct(None), "(new price)")
        self.assertEqual(daily_report._fmt_pct(12.34), "(+12.3%)")
        self.assertEqual(daily_report._fmt_pct(-0.5), "(-0.5%)")

    def test_ask_bare_prefix_and_zero(self) -> None:
        self.assertEqual(daily_report._ask(2.49), "2.49")
        self.assertEqual(daily_report._ask(0.0114), "0.0114")
        self.assertEqual(daily_report._ask(0.0), "—")
        self.assertEqual(daily_report._ask(None), "—")


if __name__ == "__main__":
    unittest.main()
