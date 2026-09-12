"""Issue #27 Finding A: the realized ask must not be quantized into ties.

`probe.pricing.route_entry` is the ONE place a route's stored `eff_per_mtok`
is produced. It used to apply `round(eff, 4)` — an ABSOLUTE decimal snap that
fixes the step at 1e-4 whatever the magnitude. Across the board's ask range
(1e-4 .. 1e-1) that leaves one or two significant figures and MANUFACTURES
ties between genuinely different routes:

    cb/deepseek-v4.1-flash    raw 0.000353017  -> stored 0.0004
    ag/gemini-3.8-flash-high  raw 0.000384080  -> stored 0.0004

A real 8.09% gap, erased. The value is load-bearing, not display: it reaches
the switcher through `probe.basis.realized` -> `resolve_realized_price`, and
the board ranks on it through `site/board.py:_iq_sort_key`, whose key is
`(eff_val, -iqps)` — so on the fabricated tie the higher IQ per $ wins and the
board LEADS WITH THE DEARER ROUTE.

These tests pin the property the fix must never lose: a stored ask is an
ordering-preserving quantization of the raw ask. The first test in
``TestTheOldLatticeIsGone`` fails outright on the pre-fix code, which is what
makes it a regression guard rather than a restatement.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from probe import basis, pricing

REPO = Path(__file__).resolve().parents[1]

# The receipted pair from #27 Finding A (raw values from the committed
# snapshot's own cost/token inputs).
INCUMBENT = "cb/deepseek-v4.1-flash"
CHALLENGER = "ag/gemini-3.8-flash-high"
RAW_INCUMBENT = 0.000353017
RAW_CHALLENGER = 0.000384080


def _stats(cost: float, tok_in: int, tok_out: int) -> dict:
    """The raw inputs `route_entry` consumes, as the builder assembles them."""
    return {
        "reqs": 100,
        "tok_in": tok_in,
        "tok_out": tok_out,
        "cached": 0,
        "cost": cost,
        "ask_in": 0.00015,
        "ask_out": 0.0009,
        "last_ts": "2026-09-12T07:17:00Z",
        "hit_ask_ratio": None,
    }


class TestTheOldLatticeIsGone:
    """The absolute 4-dp snap must not come back."""

    def test_off_lattice_ask_is_not_snapped_to_the_lattice(self):
        """FAILS on the pre-fix code: 0.000353017 used to store as 0.0004."""
        stats = _stats(cost=0.014579, tok_in=41_000_000, tok_out=298_298)
        raw = stats["cost"] / (stats["tok_in"] + stats["tok_out"]) * 1e6
        stored = pricing.route_entry(stats, {}, INCUMBENT)["eff_per_mtok"]
        assert stored is not None
        # the old quantizer's fingerprint — this is the regression
        assert stored != round(raw, 4)
        assert stored == pytest.approx(raw, rel=1e-6)

    def test_the_receipted_pair_does_not_compare_equal(self):
        """The two cheapest routes on the board must not tie."""
        a = pricing.sig_round(RAW_INCUMBENT)
        b = pricing.sig_round(RAW_CHALLENGER)
        assert a != b
        assert a < b  # the incumbent is genuinely the cheaper route

    def test_distinct_raw_asks_never_collapse(self):
        """A sweep of asks across the board's whole range stays injective."""
        raws = [1e-4, 1.5e-4, 2.7e-4, 3.53e-4, 3.84e-4, 4.564e-4, 7.9e-3,
                9.1e-3, 2e-2, 9.2e-2]
        stored = [pricing.sig_round(r) for r in raws]
        assert len(set(stored)) == len(raws)
        assert stored == sorted(stored)  # monotonicity preserved

    def test_no_stored_ask_lands_on_the_lattice(self):
        """The 1e-4 lattice must not capture arbitrary raw asks."""
        for raw in (0.000353017, 0.000384080, 0.000456428, 0.007855460):
            stored = pricing.sig_round(raw)
            assert abs(stored * 1e4 - round(stored * 1e4)) > 1e-9, raw


class TestSigRoundContract:
    """The quantizer's own edge cases."""

    @staticmethod
    def _half_quantum(value: float, sig: int = pricing.EFF_SIG_FIGS) -> float:
        """The largest error a `sig`-figure round can leave, for this value.

        value = m x 10^e with 1 <= m < 10; the last kept digit sits at
        10^(e - sig + 1), so the half-quantum is half of that.
        """
        return 0.5 * 10.0 ** (math.floor(math.log10(abs(value))) - sig + 1)

    @pytest.mark.parametrize("value", [None, 0.0])
    def test_none_and_zero_pass_through(self, value):
        assert pricing.sig_round(value) == value

    def test_non_finite_passes_through(self):
        assert math.isinf(pricing.sig_round(float("inf")))

    @pytest.mark.parametrize("value", [
        0.000353017, 0.000384080, 0.092, 1_234_567.89,
        4.6569931072098897e-05, 0.007855460,
    ])
    def test_error_never_exceeds_the_half_quantum(self, value):
        """The defining property: a sig-fig round moves a value by at most
        half of its last kept digit — and by proportionally less as the
        value shrinks, which is exactly what the absolute snap could not do."""
        got = pricing.sig_round(value)
        assert abs(got - value) <= self._half_quantum(value)

    @pytest.mark.parametrize("value", [
        0.000353017, 0.000384080, 0.007855460, 0.092,
    ])
    def test_in_the_ask_range_it_beats_the_old_snap(self, value):
        """Across the board's ask range the old `round(x, 4)` imposed up to
        5e-5 of error — 14.2% of the cheapest ask. The new form is finer."""
        got = pricing.sig_round(value)
        assert abs(got - value) < 5e-5

    def test_quantum_scales_with_magnitude(self):
        """A tiny ask keeps far finer resolution than a large one — the
        property the absolute snap destroyed."""
        tiny, large = 4.6569931072098897e-05, 0.0921234567
        assert self._half_quantum(tiny) < self._half_quantum(large)
        assert abs(pricing.sig_round(tiny) - tiny) < abs(pricing.sig_round(large) - large)

    def test_negative_values_round_on_magnitude(self):
        assert pricing.sig_round(-0.000353017) == pytest.approx(-0.000353017, rel=1e-6)


class TestTheDecisionPathSeesFullPrecision:
    """`basis.realized` and the board rank must read the unsnapped value."""

    def _payload(self) -> dict:
        return {
            "routes": {
                INCUMBENT: {
                    "eff_per_mtok": pricing.sig_round(RAW_INCUMBENT),
                    "ask_in": 0.00015, "ask_out": 0.0009, "reqs": 525,
                    "tok_in": 41_000_000, "tok_out": 298_298,
                    "cost_usdc": "0.014579", "source": "usage-logs",
                },
                CHALLENGER: {
                    "eff_per_mtok": pricing.sig_round(RAW_CHALLENGER),
                    "ask_in": 0.00075, "ask_out": 0.00225, "reqs": 1792,
                    "tok_in": 115_000_000, "tok_out": 660_912,
                    "cost_usdc": "0.044423", "source": "usage-logs",
                },
            },
            "perf": {"models": {}},
        }

    def test_basis_realized_returns_the_unsnapped_value(self):
        payload = self._payload()
        assert basis.realized(payload, INCUMBENT) == pricing.sig_round(RAW_INCUMBENT)
        assert basis.realized(payload, CHALLENGER) == pricing.sig_round(RAW_CHALLENGER)
        assert basis.realized(payload, INCUMBENT) < basis.realized(payload, CHALLENGER)

    def test_incumbent_bar_is_not_tied_by_the_challenger(self):
        """The bar picks the cheaper route — a tie would make it ambiguous."""
        payload = self._payload()
        bar = basis.incumbent_bar(payload["routes"], [INCUMBENT, CHALLENGER])
        assert bar == pricing.sig_round(RAW_INCUMBENT)
        assert bar < pricing.sig_round(RAW_CHALLENGER)

    def test_marginal_is_quantized_by_significant_figures_too(self):
        """The scatter's X value shares the decision quantizer."""
        raw = 0.000353017
        assert pricing.sig_round(raw) != round(raw, 4)
        assert pricing.sig_round(raw) == pytest.approx(raw, rel=1e-6)


class TestTheCommittedSnapshotIsClean:
    """End-to-end: replay the committed snapshot through the real builder."""

    @pytest.fixture(scope="class")
    def receipt(self) -> dict:
        from scripts import ask_precision_receipt as apr
        snapshot = json.loads((REPO / "data" / "pricing.json").read_text())
        rows = apr.replay(snapshot)
        return {"rows": rows, "collapses": apr.collapses(rows),
                "lattice": apr.lattice_count(rows)}

    def test_no_pair_of_distinct_raw_asks_shares_a_stored_value(self, receipt):
        assert receipt["collapses"] == [], [
            (a["route"], b["route"]) for a, b in receipt["collapses"]
        ]

    def test_the_four_named_routes_keep_their_own_values(self, receipt):
        by_route = {r["route"]: r for r in receipt["rows"]}
        for route in ("cb/deepseek-v4.1-flash", "ag/gemini-3.7-flash-high",
                      "ag/gemini-3.8-flash-high", "ali/deepseek-v4-flash-0731"):
            row = by_route[route]
            assert row["stored"] == pytest.approx(row["raw"], rel=1e-6), route

    def test_the_lattice_captures_nothing(self, receipt):
        assert receipt["lattice"] == 0, receipt["lattice"]

    def test_the_board_leads_with_the_cheaper_route(self, receipt):
        """The receipted defect: the dearer route used to lead the board."""
        from scripts import ask_precision_receipt as apr
        snapshot = json.loads((REPO / "data" / "pricing.json").read_text())
        payload = json.loads(json.dumps(snapshot))
        for row in receipt["rows"]:
            payload["routes"][row["route"]]["eff_per_mtok"] = row["stored"]
        order = [route for route, _eff in apr.board_order(payload, False)]
        assert order.index(INCUMBENT) < order.index(CHALLENGER)
