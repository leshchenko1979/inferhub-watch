from __future__ import annotations

import unittest

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "site"))

import rundata  # noqa: E402


class RundataBoardTests(unittest.TestCase):
    def test_scoring_short_and_rule(self) -> None:
        self.assertEqual(rundata.scoring_short("stream_tools"), "tools")
        self.assertEqual(rundata.scoring_short("cache_tools"), "cache")
        self.assertEqual(rundata.scoring_short("ru_mojibake"), "mojibake")
        self.assertEqual(
            rundata.scoring_rule(["stream_tools", "cache_tools", "ru_mojibake"]),
            "3/3: tools + cache + mojibake",
        )
        self.assertEqual(
            rundata.scoring_rule(["stream_tools", "cache_tools"]),
            "2/2: tools + cache",
        )

    def test_aliases_safe_first_keeps_relative_order(self) -> None:
        run = {
            "cells": [
                {"alias": "a", "check_id": "stream_tools", "status": "fail"},
                {"alias": "a", "check_id": "cache_tools", "status": "fail"},
                {"alias": "b", "check_id": "stream_tools", "status": "pass"},
                {"alias": "b", "check_id": "cache_tools", "status": "pass"},
                {"alias": "c", "check_id": "stream_tools", "status": "pass"},
                {"alias": "c", "check_id": "cache_tools", "status": "fail"},
            ]
        }
        ids = ["stream_tools", "cache_tools"]
        self.assertEqual(
            rundata.aliases_safe_first(["a", "b", "c"], run, ids),
            ["b", "a", "c"],
        )
        self.assertEqual(rundata.scoring_failed_ids(run, "c", ids), ["cache_tools"])

    def test_origin_gloss(self) -> None:
        self.assertEqual(
            rundata.origin_label({"origin": "github-actions"}), "Actions · CI"
        )
        self.assertEqual(rundata.origin_label({"origin": "local-seed"}), "seed · fixture")

    def test_display_specs_put_scoring_first(self) -> None:
        registry = [
            {"id": "usage_pricing", "scores_rank": False},
            {"id": "stream_tools", "scores_rank": True},
        ]
        self.assertEqual(
            [s["id"] for s in rundata.display_specs(registry)],
            ["stream_tools", "usage_pricing"],
        )


if __name__ == "__main__":
    unittest.main()
