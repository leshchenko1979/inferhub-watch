from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from probe import run as probe_run
from probe.registry import load_candidates, repo_root


class LoadCandidatesTests(unittest.TestCase):
    def test_missing_file_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load_candidates(Path(tmp) / "nope.toml"), [])

    def test_parses_groups_and_skips_unusable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "candidates.toml"
            path.write_text(
                '[[candidate]]\n'
                'model = "deepseek-v4-pro"\n'
                'routes = ["ocg/deepseek-v4-pro", "cbcn/deepseek-v4-pro"]\n'
                "\n"
                '[[candidate]]\n'
                'model = "no-routes"\n'
                "routes = []\n"
                "\n"
                '[[candidate]]\n'
                'model = ""\n'
                'routes = ["x/y"]\n'
            )
            groups = load_candidates(path)
        self.assertEqual(groups, [
            {"model": "deepseek-v4-pro", "routes": ["ocg/deepseek-v4-pro", "cbcn/deepseek-v4-pro"]},
        ])

    def test_malformed_toml_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "candidates.toml"
            path.write_text("[[candidate\nbroken")
            self.assertEqual(load_candidates(path), [])

    def test_repo_candidates_toml_shape(self) -> None:
        groups = load_candidates()
        self.assertTrue(groups, "repo ships a non-empty candidates.toml")
        seen: set[str] = set()
        for group in groups:
            self.assertTrue(group["model"])
            for route in group["routes"]:
                self.assertIn("/", route, route)
                self.assertNotIn(route, seen, f"duplicate route {route}")
                seen.add(route)


class CandidateSweepTests(unittest.TestCase):
    def test_candidate_routes_dedupes_and_keeps_order(self) -> None:
        groups = [
            {"model": "m1", "routes": ["a/x", "b/x"]},
            {"model": "m2", "routes": ["b/x", "c/y"]},
        ]
        self.assertEqual(
            probe_run.candidate_routes(groups),
            [("a/x", "m1"), ("b/x", "m1"), ("c/y", "m2")],
        )
        self.assertEqual(probe_run.candidate_routes([]), [])

    def test_sweep_tags_every_cell(self) -> None:
        def fake_collect(client, aliases, registry):
            return (
                [
                    {"alias": aliases[0], "check_id": "stream_tools", "status": "pass"},
                    {"alias": aliases[0], "check_id": "cache_tools", "status": "fail"},
                ],
                [],
            )

        with mock.patch.object(probe_run, "collect_cells", side_effect=fake_collect):
            cells, errors = probe_run.run_candidate_sweep(
                object(), [("a/x", "m1"), ("c/y", "m2")], []
            )
        self.assertEqual(errors, [])
        self.assertEqual(len(cells), 4)
        for cell in cells:
            self.assertTrue(cell["candidate"])
        self.assertEqual({c["model"] for c in cells[:2]}, {"m1"})
        self.assertEqual({c["model"] for c in cells[2:]}, {"m2"})

    def test_balance_too_low_propagates(self) -> None:
        def raising(client, aliases, registry):
            raise probe_run.BalanceTooLow("a/x: balance too low")

        with mock.patch.object(probe_run, "collect_cells", side_effect=raising):
            with self.assertRaises(probe_run.BalanceTooLow):
                probe_run.run_candidate_sweep(object(), [("a/x", "m1")], [])


if __name__ == "__main__":
    unittest.main()
