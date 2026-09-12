"""Tests for probe.stack — the book estimator beside the floor (issue #41).

The owner ruling of 2026-09-12 ("we price the book") makes the book price a
decision input, so these tests pin the three things that make it safe to act on:
the estimator's arithmetic, its behaviour on degenerate ladders (never 0.0 for
an absent book), and the choice of estimator itself — the banned
supply-weighted mean must stay unselectable.
"""

from __future__ import annotations

import unittest

from probe import stack

class MassPointTests(unittest.TestCase):
    def test_most_populated_price_wins(self) -> None:
        ladder = [[0.00015, 7], [0.0045, 4], [0.00585, 378], [0.075, 74]]
        self.assertEqual(stack.mass_point(ladder), 0.00585)

    def test_tie_breaks_to_the_lower_price(self) -> None:
        # Two prices equally populated: the cheaper is the one reached first,
        # and a fixed tie rule keeps two pulls of one book comparable.
        self.assertEqual(stack.mass_point([[0.6, 2], [0.5, 2]]), 0.5)

    def test_zero_count_points_never_win(self) -> None:
        self.assertEqual(stack.mass_point([[0.001, 0], [0.002, 3]]), 0.002)

    def test_absent_book_is_none_never_zero(self) -> None:
        for empty in ([], None, [[0.005, 0]], ["junk"]):
            with self.subTest(empty=empty):
                self.assertIsNone(stack.mass_point(empty))

class ProviderWeightedMedianTests(unittest.TestCase):
    def test_median_provider_price(self) -> None:
        # 10 providers at 0.01, 1 at 0.02 -> cumulative crosses half at 0.01.
        self.assertEqual(
            stack.provider_weighted_median([[0.01, 10], [0.02, 1]]), 0.01)

    def test_single_huge_count_point_cannot_move_it(self) -> None:
        # The mass point IS the spike; the median is not.
        ladder = [[0.01, 1], [0.02, 1], [0.15, 998]]
        self.assertEqual(stack.mass_point(ladder), 0.15)
        self.assertEqual(stack.provider_weighted_median(ladder), 0.15)
        # ...but a spike that does NOT hold half the book leaves it put: 201
        # providers, so the median provider is the 101st, which sits at 0.02.
        spread = [[0.01, 100], [0.02, 100], [0.15, 1]]
        self.assertEqual(stack.provider_weighted_median(spread), 0.02)

    def test_zero_count_points_are_not_priced(self) -> None:
        # An advertised-but-unfilled price is not a book price (#27's rule).
        self.assertIsNone(stack.provider_weighted_median([[0.005, 0]]))
        self.assertEqual(stack.provider_weighted_median([[0.005, 0], [0.006, 4]]), 0.006)

    def test_absent_book_is_none_never_zero(self) -> None:
        for empty in ([], None, [[0.005, 0]]):
            with self.subTest(empty=empty):
                self.assertIsNone(stack.provider_weighted_median(empty))

class SupplyWeightedMeanTests(unittest.TestCase):
    def test_it_is_computable_but_banned(self) -> None:
        # Kept reproducible so the 80.3%-worse measurement can be re-derived,
        # but it must never become the selected estimator.
        self.assertIn("supply_weighted_mean", stack.BANNED_ESTIMATORS)
        self.assertNotEqual(stack.BOOK_ESTIMATOR, "supply_weighted_mean")
        self.assertAlmostEqual(
            stack.supply_weighted_mean([[0.01, 1], [0.03, 3]]), 0.025)

    def test_the_selected_estimator_is_not_banned(self) -> None:
        self.assertNotIn(stack.BOOK_ESTIMATOR, stack.BANNED_ESTIMATORS)

class BookPointTests(unittest.TestCase):
    def test_book_point_is_the_provider_weighted_median(self) -> None:
        ladder = [[0.00015, 7], [0.00585, 378], [0.075, 74]]
        self.assertEqual(stack.book_point(ladder),
                         stack.provider_weighted_median(ladder))

    def test_deepseek_shape_matches_the_measured_receipt(self) -> None:
        # The live cb/deepseek-v4.1-flash book that motivated the ruling:
        # floor 0.00015 held by a handful, mass 0.00585 held by most.
        ladder = [[0.00015, 7], [0.00135, 4], [0.0045, 4], [0.00585, 378],
                  [0.075, 74]]
        self.assertEqual(stack.book_point(ladder), 0.00585)
        self.assertEqual(stack.estimators(ladder)["floor"], 0.00015)

    def test_absent_book_is_none_never_zero(self) -> None:
        for empty in ([], None, [[0.005, 0]]):
            with self.subTest(empty=empty):
                self.assertIsNone(stack.book_point(empty))

class EstimatorsTests(unittest.TestCase):
    def test_all_four_estimators_are_reported_together(self) -> None:
        got = stack.estimators([[0.01, 1], [0.02, 3]])
        self.assertEqual(set(got), {"floor", "mass", "provider_weighted_median",
                                    "supply_weighted_mean"})
        self.assertEqual(got["floor"], 0.01)
        self.assertEqual(got["mass"], 0.02)

class DescribeTests(unittest.TestCase):
    def test_receipt_names_book_floor_and_ratio(self) -> None:
        text = stack.describe([[0.00015, 7], [0.00585, 378]])
        self.assertIn("book=0.005850@378p", text)
        self.assertIn("floor 0.000150@7p", text)
        self.assertIn("39.0x", text)

    def test_receipt_says_none_for_an_absent_book(self) -> None:
        self.assertIn("book=none", stack.describe([]))

if __name__ == "__main__":
    unittest.main()
