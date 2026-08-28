from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from probe import market, run as probe_run

NOW = datetime(2026, 8, 28, 6, 0, 0, tzinfo=timezone.utc)

# Board: one qwen route. Bar 0.005 $/M, 75% cache, 75/25 in/out mix.
ALIASES = ["ali/qwen3.8-max"]
PRICING = {
    "routes": {
        "ali/qwen3.8-max": {
            "eff_per_mtok": 0.005,
            "cache_pct": 75.0,
            "tok_in": 750,
            "tok_out": 250,
            "source": "usage-logs",
        },
    },
}
CATALOG = {
    # predicted = ask_in * (1-0.75) * 0.75 + ask_out * 0.25
    "cb/qwen3.8-max": (0.001, 0.003),   # 0.00094 -> cheaper
    "cx/qwen3.8-max": (0.002, 0.004),   # 0.00138 -> cheaper
    "cz/qwen3.8-max": (0.003, 0.020),   # 0.00556 -> NOT cheaper
    "ali/qwen3.8-max": (0.0001, 0.0001),  # board alias -> excluded
}


class FamilyTests(unittest.TestCase):
    def test_last_path_segment(self) -> None:
        self.assertEqual(market.family("cp/cline-pass/deepseek-v4-pro"), "deepseek-v4-pro")
        self.assertEqual(market.family("ali/qwen3.8-max"), "qwen3.8-max")
        self.assertEqual(market.family("solo"), "solo")


class PredictedPriceTests(unittest.TestCase):
    def test_cache_discounts_input_ask_only(self) -> None:
        self.assertAlmostEqual(
            market.predicted_usd_m(1.0, 3.0, cache_rate=0.5, w_in=0.8, w_out=0.2),
            1.0 * 0.5 * 0.8 + 3.0 * 0.2,
        )

    def test_token_weights_from_mix(self) -> None:
        self.assertEqual(market.token_weights({"tok_in": 300, "tok_out": 100}), (0.75, 0.25))

    def test_token_weights_fallback(self) -> None:
        self.assertEqual(market.token_weights({}), (0.75, 0.25))
        self.assertEqual(market.token_weights({"tok_in": 0, "tok_out": 0}), (0.75, 0.25))
        self.assertEqual(market.token_weights({"tok_in": "junk"}), (0.75, 0.25))


class IncumbentBarTests(unittest.TestCase):
    def test_cheapest_billed_wins(self) -> None:
        routes = {
            "cp/m": {"eff_per_mtok": 0.06},
            "cmc/m": {"eff_per_mtok": 0.12},
        }
        bar, entry = market.incumbent_bar(routes, ["cp/m", "cmc/m"])
        self.assertEqual(bar, 0.06)
        self.assertEqual(entry["eff_per_mtok"], 0.06)

    def test_no_billed_incumbent(self) -> None:
        bar, entry = market.incumbent_bar({"cp/m": {"eff_per_mtok": None}}, ["cp/m"])
        self.assertIsNone(bar)
        self.assertEqual(entry, {})
        self.assertEqual(market.incumbent_bar({}, ["ghost"]), (None, {}))


class ShortlistTests(unittest.TestCase):
    def _shortlist(self, catalog=None, pricing=PRICING, aliases=ALIASES,
                   proven=None, root=None, now=NOW):
        with mock.patch.object(market, "fetch_catalog", return_value=catalog or CATALOG), \
                mock.patch.object(market, "load_aliases", return_value=aliases), \
                mock.patch.object(market, "load_proven", return_value=proven or {}):
            return market.shortlist("key", pricing, root=root, now=now)

    def test_cheaper_only_top2_board_excluded(self) -> None:
        groups = self._shortlist()
        self.assertEqual(groups, [{"model": "qwen3.8-max", "routes": [
            "cb/qwen3.8-max", "cx/qwen3.8-max"]}])

    def test_top_n_cutoff(self) -> None:
        catalog = dict(CATALOG)
        catalog["cd/qwen3.8-max"] = (0.0015, 0.003)  # predicted 0.00103, ranks 2nd
        groups = self._shortlist(catalog=catalog)
        self.assertEqual(groups[0]["routes"], ["cb/qwen3.8-max", "cd/qwen3.8-max"])

    def test_proven_within_ttl_is_skipped(self) -> None:
        proven = {"cb/qwen3.8-max": {"last_probe": (NOW - timedelta(days=3)).isoformat()}}
        groups = self._shortlist(proven=proven)
        self.assertEqual(groups[0]["routes"], ["cx/qwen3.8-max"])

    def test_proven_after_ttl_is_picked_again(self) -> None:
        proven = {"cb/qwen3.8-max": {"last_probe": (NOW - timedelta(days=8)).isoformat()}}
        groups = self._shortlist(proven=proven)
        self.assertIn("cb/qwen3.8-max", groups[0]["routes"])

    def test_repricing_does_not_lift_ttl(self) -> None:
        # absolute TTL: same proven entry, cheaper asks in the catalog
        proven = {"cb/qwen3.8-max": {"last_probe": (NOW - timedelta(days=1)).isoformat()}}
        catalog = dict(CATALOG)
        catalog["cb/qwen3.8-max"] = (0.0001, 0.0001)  # dramatic price drop
        groups = self._shortlist(catalog=catalog, proven=proven)
        self.assertNotIn("cb/qwen3.8-max", groups[0]["routes"])

    def test_family_without_billed_incumbent_is_skipped(self) -> None:
        pricing = {"routes": {"zai/glm-5.3": {"eff_per_mtok": None}}}
        groups = self._shortlist(
            aliases=["zai/glm-5.3"], pricing=pricing,
            catalog={"cb/glm-5.3": (0.001, 0.001)},
        )
        self.assertEqual(groups, [])

    def test_missing_pricing_means_no_bar(self) -> None:
        self.assertEqual(self._shortlist(pricing=None), [])


class ProvenTests(unittest.TestCase):
    def test_record_and_load_round_trip(self) -> None:
        run_payload = {
            "started_at": "2026-08-28T06:00:00+00:00",
            "cells": [
                {"alias": "ali/qwen3.8-max", "check_id": "core", "status": "pass"},
                {"alias": "cb/m", "check_id": "core", "status": "fail", "candidate": True},
                {"alias": "cb/m", "check_id": "cache", "status": "skipped", "candidate": True},
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = market.record_proven(run_payload, root=Path(tmp))
            self.assertEqual(path, Path(tmp) / "data" / "proven.json")
            proven = market.load_proven(Path(tmp))
        self.assertEqual(set(proven), {"cb/m"})  # board cells stay out
        self.assertEqual(proven["cb/m"]["last_probe"], "2026-08-28T06:00:00+00:00")
        self.assertEqual(proven["cb/m"]["statuses"], {"core": "fail", "cache": "skipped"})

    def test_record_updates_existing_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            market.record_proven({
                "started_at": "2026-08-20T06:00:00+00:00",
                "cells": [{"alias": "cb/m", "check_id": "core", "status": "fail",
                           "candidate": True}],
            }, root=root)
            market.record_proven({
                "started_at": "2026-08-28T06:00:00+00:00",
                "cells": [{"alias": "cb/m", "check_id": "core", "status": "pass",
                           "candidate": True}],
            }, root=root)
            proven = market.load_proven(root)
        self.assertEqual(proven["cb/m"]["last_probe"], "2026-08-28T06:00:00+00:00")
        self.assertEqual(proven["cb/m"]["statuses"]["core"], "pass")

    def test_proven_recent_boundaries(self) -> None:
        def proven_for(delta):
            return {"r": {"last_probe": (NOW - delta).isoformat()}}
        self.assertTrue(market.proven_recent(proven_for(timedelta(days=6, hours=23)), "r", NOW))
        self.assertFalse(market.proven_recent(proven_for(timedelta(days=7, hours=1)), "r", NOW))
        self.assertFalse(market.proven_recent({}, "r", NOW))
        self.assertFalse(market.proven_recent({"r": {"last_probe": "junk"}}, "r", NOW))
        # naive timestamps read as UTC
        naive = {"r": {"last_probe": (NOW - timedelta(days=1)).replace(tzinfo=None).isoformat()}}
        self.assertTrue(market.proven_recent(naive, "r", NOW))

    def test_malformed_proven_file_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").mkdir()
            (root / "data" / "proven.json").write_text("{broken")
            self.assertEqual(market.load_proven(root), {})


class DryRunTests(unittest.TestCase):
    def _root_with_pricing(self, tmp) -> Path:
        root = Path(tmp)
        (root / "data").mkdir()
        (root / "data" / "pricing.json").write_text(json.dumps(PRICING))
        return root

    def test_dry_run_prints_ranking_and_shortlist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root_with_pricing(tmp)
            buf = io.StringIO()
            with mock.patch.object(market, "fetch_catalog", return_value=CATALOG), \
                    mock.patch.object(market, "load_aliases", return_value=ALIASES), \
                    mock.patch.dict(os.environ, {"INFERHUB_API_KEY": "k"}), \
                    redirect_stdout(buf):
                code = market.main(["--dry-run"], root=root)
        out = buf.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("qwen3.8-max", out)
        self.assertIn("predicted $", out)
        self.assertIn("cb/qwen3.8-max", out)
        self.assertIn("SHORTLIST", out)
        self.assertIn("skip — not cheaper", out)
        self.assertIn("shortlist total: 2", out)
        self.assertNotIn("ali/qwen3.8-max  ", out)  # board alias never ranked

    def test_dry_run_proven_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root_with_pricing(tmp)
            (root / "data" / "proven.json").write_text(json.dumps(
                {"cb/qwen3.8-max": {"last_probe": NOW.isoformat()}}))
            buf = io.StringIO()
            with mock.patch.object(market, "fetch_catalog", return_value=CATALOG), \
                    mock.patch.object(market, "load_aliases", return_value=ALIASES), \
                    mock.patch.dict(os.environ, {"INFERHUB_API_KEY": "k"}), \
                    mock.patch.object(market, "load_proven", wraps=market.load_proven), \
                    redirect_stdout(buf):
                code = market.main(["--dry-run"], root=root)
        self.assertEqual(code, 0)
        self.assertIn("skip — proven <7d", buf.getvalue())

    def test_missing_key_exits_2(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(market.main(["--dry-run"]), 2)

    def test_usage_without_flag(self) -> None:
        self.assertEqual(market.main([]), 2)


class CandidateSweepTests(unittest.TestCase):
    """Moved from the retired test_candidates.py — the sweep shape survives."""

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
                    {"alias": aliases[0], "check_id": "core", "status": "pass"},
                    {"alias": aliases[0], "check_id": "cache", "status": "fail"},
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
