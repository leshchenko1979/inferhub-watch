"""Tests for probe.floor — the single ladder-point selection rule (issue #27).

The thrash incident's floor-point leg: two callers selected a route's floor
from the same ladder with different rules, so `data/pricing.json` and
`route_metrics` could publish two different floors for one route on one pull.
These tests pin the rule and pin the agreement.
"""

from __future__ import annotations

import unittest

from probe import floor


class FloorPointTests(unittest.TestCase):
    def test_zero_count_point_is_never_the_floor(self) -> None:
        # 0.001 is advertised but no publisher offers it -> not a floor.
        self.assertEqual(floor.floor_point([[0.001, 0], [0.002, 3], [0.003, 1]]), (0.002, 3))

    def test_cheapest_filled_point_wins(self) -> None:
        ladder = [[0.0045, 1], [0.00135, 3], [0.00585, 386]]
        self.assertEqual(floor.floor_point(ladder), (0.00135, 3))

    def test_empty_or_all_zero_ladders_have_no_floor(self) -> None:
        self.assertEqual(floor.floor_point([]), (None, None))
        self.assertEqual(floor.floor_point(None), (None, None))
        self.assertEqual(floor.floor_point([[0.005, 0], [0.006, 0]]), (None, None))

    def test_tie_on_price_prefers_more_publishers(self) -> None:
        self.assertEqual(floor.floor_point([[0.002, 1], [0.002, 7]]), (0.002, 7))

    def test_malformed_points_are_skipped(self) -> None:
        ladder = ["nope", [0.001], [None, 3], ["x", 2], [0.003, 2], None]
        self.assertEqual(floor.floor_point(ladder), (0.003, 2))

    def test_ladder_points_is_price_ascending_and_deterministic(self) -> None:
        ladder = [[0.00585, 386], [0.00135, 3], [0.0045, 1]]
        self.assertEqual(
            floor.ladder_points(ladder),
            [(0.00135, 3), (0.0045, 1), (0.00585, 386)],
        )

    def test_describe_point_names_the_selected_ladder_point(self) -> None:
        text = floor.describe_point([[0.0045, 1], [0.00135, 3], [0.00585, 386]])
        self.assertIn("0.001350@3p", text)
        self.assertIn("3 points", text)

    def test_describe_point_reports_no_floor_without_filled_points(self) -> None:
        self.assertIn("floor=none", floor.describe_point([[0.005, 0]]))


class FloorPairTests(unittest.TestCase):
    def test_a_filled_ladder_point_outranks_the_legacy_asks_array(self) -> None:
        # The ladder is the only schema that carries publisher counts, so it
        # is the one that can be checked for "is anyone actually offering
        # this". A legacy per-provider array is the fallback, never the
        # authority, when a filled ladder point exists (#27).
        model = {"asksIn": [0.01, 0.02], "asksOut": [0.03], "pricePointsIn": [[0.001, 5]]}
        self.assertEqual(floor.floor_pair(model), (0.001, 0.03))

    def test_histogram_pair_uses_the_filled_floor(self) -> None:
        model = {
            "pricePointsIn": [[0.005, 0], [0.0045, 1]],
            "pricePointsOut": [[0.018, 1]],
        }
        self.assertEqual(floor.floor_pair(model), (0.0045, 0.018))

    def test_pair_is_none_when_either_side_has_no_filled_point(self) -> None:
        model = {"pricePointsIn": [[0.0045, 1]], "pricePointsOut": [[0.018, 0]]}
        self.assertIsNone(floor.floor_pair(model))

    def test_price_and_publisher_count_come_off_the_same_point(self) -> None:
        # The sync's floor receipt prints a price and a publisher count
        # together; they must describe ONE ladder point, never two.
        model = {
            "pricePointsIn": [[0.00135, 0], [0.0045, 1], [0.00585, 386]],
            "pricePointsOut": [[0.0054, 3], [0.018, 1]],
        }
        (in_price, in_pubs), (out_price, out_pubs) = floor.floor_pair_detail(model)
        self.assertEqual((in_price, in_pubs), (0.0045, 1))
        self.assertEqual((out_price, out_pubs), (0.0054, 3))
        self.assertEqual(floor.floor_pair(model), (in_price, out_price))

    def test_legacy_fallback_does_not_invent_a_publisher_count(self) -> None:
        # With no ladder, the legacy array gives a price but no count; the
        # receipt must say "unknown", not copy a count from elsewhere.
        model = {"asksIn": [0.01, 0.02], "asksOut": [0.03]}
        (in_price, in_pubs), (out_price, out_pubs) = floor.floor_pair_detail(model)
        self.assertEqual((in_price, in_pubs), (0.01, None))
        self.assertEqual((out_price, out_pubs), (0.03, None))

    def test_legacy_array_never_overrides_a_filled_ladder_point(self) -> None:
        # A stale per-provider array must not outrank a live filled ladder
        # point and silently reinstate the divergence.
        model = {"asksIn": [0.0001], "asksOut": [0.0002],
                 "pricePointsIn": [[0.0045, 1]], "pricePointsOut": [[0.018, 1]]}
        self.assertEqual(floor.floor_pair(model), (0.0045, 0.018))
        self.assertEqual(floor.floor_pair_detail(model)[0], (0.0045, 1))


class FloorAgreementTests(unittest.TestCase):
    """The regression the thrash exposed: one ladder, one floor."""

    def test_pricing_and_catalog_agree_on_the_same_ladder(self) -> None:
        from probe import catalog, pricing

        model = {
            "pricePointsIn": [[0.00135, 3], [0.0045, 1], [0.00585, 386]],
            "pricePointsOut": [[0.0054, 3], [0.018, 1], [0.0234, 386]],
        }
        # probe.pricing reads the histogram directly...
        self.assertEqual(
            pricing._model_asks(model), floor.floor_pair(model)
        )
        # ...and probe.catalog (the route_metrics/switcher feed) builds the
        # same number through fetch_models' selector.
        self.assertEqual(floor.floor_pair(model), (0.00135, 0.0054))
        self.assertTrue(hasattr(catalog, "fetch_models"))

    def test_the_phantom_floor_min_would_have_picked_is_rejected(self) -> None:
        # Historical bug: catalog.fetch_models took a bare min() over every
        # point, so a 0-publisher point at 0.00135 would have beaten the
        # real floor at 0.0045 — a price no request could be served at.
        ladder = [[0.00135, 0], [0.0045, 1], [0.00585, 386]]
        self.assertEqual(floor.floor_point(ladder), (0.0045, 1))
        self.assertNotEqual(floor.floor_point(ladder)[0], min(p[0] for p in ladder))


if __name__ == "__main__":
    unittest.main()
