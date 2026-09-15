"""Tests for probe/basis.py — the single money-basis owner (P1a)."""
from __future__ import annotations

import unittest
import unittest.mock

from probe import basis


def _payload(routes: dict) -> dict:
    return {"routes": routes}


class RealizedMarginalTests(unittest.TestCase):
    def test_realized_reads_eff(self) -> None:
        p = _payload({"r/a": {"eff_per_mtok": 0.001}})
        self.assertEqual(basis.realized(p, "r/a"), 0.001)

    def test_realized_none_for_unknown_route(self) -> None:
        self.assertIsNone(basis.realized(_payload({}), "ghost"))

    def test_marginal_reads_marginal(self) -> None:
        p = _payload({"r/a": {"marginal_per_mtok": 0.002}})
        self.assertEqual(basis.marginal(p, "r/a"), 0.002)


class BoardBasisTests(unittest.TestCase):
    def test_realized_basis_when_not_use_proj(self) -> None:
        p = _payload({"r/a": {"eff_per_mtok": 0.001}})
        self.assertEqual(
            basis.board_basis(p, "r/a", [], use_proj=False), 0.001)

    def test_projected_when_gate_passes(self) -> None:
        p = _payload({"r/a": {"eff_per_mtok": 0.5, "ask_in": 0.01,
                              "ask_out": 0.03, "reqs": 100}})
        dated = [{"routes": {"r/a": {"hit": 0.9}}}]
        with unittest.mock.patch.object(basis.official_compare,
                                        "projection_hit",
                                        return_value=(0.9, "ok")):
            got = basis.board_basis(p, "r/a", dated, use_proj=True)
        self.assertIsNotNone(got)

    def test_falls_back_to_realized_when_projection_none(self) -> None:
        p = _payload({"r/a": {"eff_per_mtok": 0.001}})
        with unittest.mock.patch.object(basis.official_compare,
                                        "projection_hit",
                                        return_value=(None, "no-data")):
            got = basis.board_basis(p, "r/a", [{"anything": 1}], use_proj=True)
        self.assertEqual(got, 0.001)


class ChartPriceTests(unittest.TestCase):
    def test_marginal_wins_with_tag(self) -> None:
        p = _payload({"r/a": {"eff_per_mtok": 0.5,
                              "marginal_per_mtok": 0.004}})
        self.assertEqual(basis.chart_price(p, "r/a"), (0.004, "marginal"))

    def test_eff_fallback_with_tag(self) -> None:
        p = _payload({"r/a": {"eff_per_mtok": 0.008}})
        self.assertEqual(basis.chart_price(p, "r/a"), (0.008, "eff"))

    def test_none_without_any_price(self) -> None:
        self.assertEqual(basis.chart_price(_payload({}), "ghost"), (None, ""))


class RouteBasesTests(unittest.TestCase):
    """route_bases: the per-route prices the panels must mirror."""

    def test_every_route_gets_a_row(self) -> None:
        p = _payload({"r/a": {"eff_per_mtok": 0.001},
                      "r/b": {"eff_per_mtok": 0.002}})
        with unittest.mock.patch.object(basis, "projected", return_value=None):
            rows = basis.route_bases(p, [])
        self.assertEqual([r["route"] for r in rows], ["r/a", "r/b"])
        self.assertEqual(rows[0]["realized"], 0.001)
        self.assertIsNone(rows[0]["projected"])

    def test_projected_carried_through(self) -> None:
        p = _payload({"r/a": {"eff_per_mtok": 0.001}})
        with unittest.mock.patch.object(basis, "projected", return_value=0.0007):
            rows = basis.route_bases(p, [])
        self.assertEqual(rows[0]["projected"], 0.0007)

    def test_no_routes_is_empty(self) -> None:
        self.assertEqual(basis.route_bases(_payload({}), []), [])

class GateStateTests(unittest.TestCase):
    """gate_state: one verdict, read through the money-basis owner."""

    def test_reads_committed_snapshots(self) -> None:
        verdict = {"n": 14, "land": 12, "share": 0.857, "tol": 0.15,
                   "min_n": 10, "pass": True}
        with unittest.mock.patch.object(basis.pricing, "dated_snapshots",
                                        return_value=[("2026-09-11", {})]), \
                unittest.mock.patch.object(basis.official_compare,
                                           "projection_gate",
                                           return_value=verdict) as gate:
            self.assertEqual(basis.gate_state(), verdict)
        gate.assert_called_once()

class IncumbentBarTests(unittest.TestCase):
    def test_cheapest_realized_wins(self) -> None:
        routes = {"a": {"eff_per_mtok": 0.01}, "b": {"eff_per_mtok": 0.002}}
        self.assertEqual(basis.incumbent_bar(routes, ["a", "b"]), 0.002)

    def test_none_when_no_billing(self) -> None:
        routes = {"a": {"eff_per_mtok": None}}
        self.assertIsNone(basis.incumbent_bar(routes, ["a"]))


class FleetCalibrationTests(unittest.TestCase):
    """fleet_calibration_multiplier: online causal median level correction (Strategy 2)."""

    def test_empty_or_single_snapshot_returns_one(self) -> None:
        self.assertEqual(basis.fleet_calibration_multiplier([]), 1.0)
        self.assertEqual(basis.fleet_calibration_multiplier([("2026-09-01", {})]), 1.0)

    def test_causal_multiplier_scales_projection_and_preserves_ranking(self) -> None:
        # Construct snapshots where realized is consistently 1.5x projected
        d1 = ("2026-09-01", {
            "routes": {
                "r/a": {"ask_in": 0.01, "ask_out": 0.02, "tok_in": 1000, "tok_out": 1000, "cache_pct": 0, "reqs": 10},
                "r/b": {"ask_in": 0.02, "ask_out": 0.04, "tok_in": 1000, "tok_out": 1000, "cache_pct": 0, "reqs": 10},
            }
        })
        d2 = ("2026-09-02", {
            "routes": {
                "r/a": {"ask_in": 0.01, "ask_out": 0.02, "tok_in": 1000, "tok_out": 1000, "cache_pct": 0, "reqs": 10, "eff_per_mtok": 0.0225},  # raw proj is 0.015 -> ratio 1.5
                "r/b": {"ask_in": 0.02, "ask_out": 0.04, "tok_in": 1000, "tok_out": 1000, "cache_pct": 0, "reqs": 10, "eff_per_mtok": 0.0450},  # raw proj is 0.030 -> ratio 1.5
            }
        })
        d3 = ("2026-09-03", {
            "routes": {
                "r/a": {"ask_in": 0.01, "ask_out": 0.02, "tok_in": 1000, "tok_out": 1000, "cache_pct": 0, "reqs": 10, "eff_per_mtok": 0.0225},
                "r/b": {"ask_in": 0.02, "ask_out": 0.04, "tok_in": 1000, "tok_out": 1000, "cache_pct": 0, "reqs": 10, "eff_per_mtok": 0.0450},
            }
        })
        d4 = ("2026-09-04", {
            "routes": {
                "r/a": {"eff_per_mtok": 0.0225},
                "r/b": {"eff_per_mtok": 0.0450},
            }
        })
        dated = [d1, d2, d3, d4]
        # At min_pairs=3, ratio should be exactly 1.5
        kappa = basis.fleet_calibration_multiplier(dated, min_pairs=3)
        self.assertAlmostEqual(kappa, 1.5, places=3)

        # Verify projected() uses calibrated=True by default and scales by kappa
        p_uncal = basis.projected(d1[1], "r/a", dated, calibrated=False)
        p_cal = basis.projected(d1[1], "r/a", dated, calibrated=True)
        self.assertIsNotNone(p_uncal)
        self.assertIsNotNone(p_cal)
        self.assertAlmostEqual(p_cal, p_uncal * kappa, places=4)






if __name__ == "__main__":
    unittest.main()
