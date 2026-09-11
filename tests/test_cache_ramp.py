"""Tests for probe/cache_ramp.py — the output path must derive from the run date.

The utility used to hardcode data/cache_ramp_20260905.json, so a re-run on a
later day overwrote a record whose name asserted a different date. The path is
now a pure function of the run date (UTC); these tests pin that contract with
no network and no probe.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timezone

from probe.cache_ramp import output_path


class OutputPathTests(unittest.TestCase):
    def test_two_dates_yield_two_filenames(self) -> None:
        a = output_path(datetime(2026, 9, 5, tzinfo=timezone.utc))
        b = output_path(datetime(2026, 9, 11, tzinfo=timezone.utc))
        self.assertNotEqual(a, b)

    def test_filename_follows_the_dated_shape(self) -> None:
        p = output_path(datetime(2026, 9, 5, tzinfo=timezone.utc))
        self.assertEqual(p.name, "cache_ramp_20260905.json")
        self.assertEqual(p.parent.name, "data")

    def test_default_is_utc_today(self) -> None:
        expected = f"cache_ramp_{datetime.now(timezone.utc):%Y%m%d}.json"
        self.assertEqual(output_path().name, expected)

if __name__ == "__main__":
    unittest.main()
