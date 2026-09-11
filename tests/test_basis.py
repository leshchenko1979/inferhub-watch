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
        verdict = {"n": 14, "land": 9, "share": 0.643, "bar": 0.8,
                   "min_n": 10, "rho_median": 0.9, "pass": False}
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





if __name__ == "__main__":
    unittest.main()
