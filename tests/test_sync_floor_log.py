"""Tests for the per-route floor-point receipt in the hourly sync (issue #27).

The thrash incident's floor leg had two halves: `probe/catalog.py` and
`probe/pricing.py` selected a route's floor from the same ladder by different
rules (fixed in tests/test_floor.py), and *neither* pull said which ladder
point it had chosen, so a floor that moved between syncs was invisible until
it had already re-ranked the board. These tests pin the receipt line and the
flip flag that makes the move visible on the pull that causes it.
"""

from __future__ import annotations

import unittest
from unittest import mock

from scripts import sync_usage_logs


class _FakeCursor:
    """Minimal DBAPI cursor: records writes, answers the previous-floor read."""

    def __init__(self, previous: list[tuple[str, object]]) -> None:
        self.previous = previous
        self.writes: list[tuple[str, tuple]] = []

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def execute(self, sql: str, params: tuple | None = None) -> None:
        self.writes.append((" ".join(sql.split()), params or ()))

    def fetchall(self) -> list[tuple[str, object]]:
        return self.previous


class _FakeConn:
    def __init__(self, previous: list[tuple[str, object]]) -> None:
        self.cur = _FakeCursor(previous)

    def cursor(self) -> _FakeCursor:
        return self.cur


class FloorPointReceiptTests(unittest.TestCase):
    def test_one_line_per_route_naming_the_point_and_its_publishers(self) -> None:
        models = {
            "cb/deepseek-v4.1-flash": {
                "ask_in": 0.00135, "ask_out": 0.0054,
                "ask_in_publishers": 3, "ask_out_publishers": 3,
            },
            "ag/gemini-3.8-flash-high": {
                "ask_in": 0.00075, "ask_out": 0.00375,
                "ask_in_publishers": 2, "ask_out_publishers": 2,
            },
        }
        with mock.patch.object(sync_usage_logs, "print") as out:
            for route, m in models.items():
                sync_usage_logs._log_floor_point(route, m)
        lines = [c.args[0] for c in out.call_args_list]
        self.assertEqual(len(lines), len(models), "exactly one line per route")
        stripped = [l.strip() for l in lines]
        self.assertIn("floor point cb/deepseek-v4.1-flash: "
                      "in=0.001350@3p out=0.005400@3p", stripped)
        self.assertIn("floor point ag/gemini-3.8-flash-high: "
                      "in=0.000750@2p out=0.003750@2p", stripped)

    def test_a_moved_floor_is_flagged_on_the_pull_that_moves_it(self) -> None:
        # The incident's own numbers: deepseek's published floor ran
        # 0.0015 -> 0.00015 -> 0.0045 across three pulls, unnoticed.
        with mock.patch.object(sync_usage_logs, "print") as out:
            sync_usage_logs._log_floor_point(
                "cb/deepseek-v4.1-flash",
                {"ask_in": 0.0045, "ask_out": 0.018,
                 "ask_in_publishers": 1, "ask_out_publishers": 1},
                previous=0.00015,
            )
        line = out.call_args_list[0].args[0]
        self.assertIn("FLOOR FLIP from 0.000150", line)
        self.assertIn("30.00x", line)

    def test_an_unchanged_floor_is_not_flagged(self) -> None:
        with mock.patch.object(sync_usage_logs, "print") as out:
            sync_usage_logs._log_floor_point(
                "cb/deepseek-v4.1-flash",
                {"ask_in": 0.00135, "ask_out": 0.0054,
                 "ask_in_publishers": 3, "ask_out_publishers": 3},
                previous=0.00135,
            )
        self.assertNotIn("FLOOR FLIP", out.call_args_list[0].args[0])

    def test_a_route_with_no_filled_point_says_so_rather_than_printing_a_price(self) -> None:
        # The phantom-floor defect: a route whose every ladder point has zero
        # publishers must publish no floor at all, never the cheapest phantom.
        with mock.patch.object(sync_usage_logs, "print") as out:
            sync_usage_logs._log_floor_point(
                "cb/phantom", {"ask_in": None, "ask_out": None,
                               "ask_in_publishers": None,
                               "ask_out_publishers": None})
        line = out.call_args_list[0].args[0]
        self.assertIn("none (no filled ladder point)", line)
        self.assertNotIn("in=", line)

    def test_the_sync_loop_emits_the_receipt_for_every_upserted_route(self) -> None:
        models = {
            "cb/deepseek-v4.1-flash": {
                "ask_in": 0.00135, "ask_out": 0.0054,
                "ask_in_publishers": 3, "ask_out_publishers": 3,
            },
            "ag/gemini-3.8-flash-high": {
                "ask_in": 0.00075, "ask_out": 0.00375,
                "ask_in_publishers": 2, "ask_out_publishers": 2,
            },
        }
        conn = _FakeConn([("cb/deepseek-v4.1-flash", 0.0015)])
        with mock.patch.object(sync_usage_logs, "print") as out:
            n = sync_usage_logs.sync_route_metrics(conn, live_models=models)
        self.assertEqual(n, 2)
        lines = [c.args[0] for c in out.call_args_list
                 if c.args and "floor point" in str(c.args[0])]
        self.assertEqual(len(lines), 2, "one receipt per upserted route")
        # the route whose floor moved against the stored row is the flagged one
        self.assertTrue(any("FLOOR FLIP" in l for l in lines))


if __name__ == "__main__":
    unittest.main()
