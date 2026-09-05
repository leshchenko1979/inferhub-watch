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

    def test_dated_snapshots_map_to_board_family(self) -> None:
        self.assertEqual(market.family("ali/deepseek-v4-flash-0731"), "deepseek-v4-flash")
        self.assertEqual(market.family("ali/deepseek-v4-pro-0813"), "deepseek-v4-pro")

    def test_flash_keeps_its_own_family(self) -> None:
        # owner rule: glm-5.3-flash is never grouped with glm-5.3
        self.assertEqual(market.family("cbcn/glm-5.3-flash"), "glm-5.3-flash")
        self.assertEqual(market.family("zai/glm-5.3-flash"), "glm-5.3-flash")

    def test_unmapped_tails_keep_their_own_family(self) -> None:
        # board aliases (unversioned) and unrelated models are untouched
        self.assertEqual(market.family("cbcn/glm-5.3"), "glm-5.3")
        self.assertEqual(market.family("zai/glm-5.2"), "glm-5.2")
        self.assertEqual(market.family("cbcn/kimi-k2.7-code"), "kimi-k2.7-code")


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


class AskBarTests(unittest.TestCase):
    def test_worst_case_billing_of_asks(self) -> None:
        # cache 0, fallback 75/25 mix — the incumbent's raw asks
        catalog = {"zai/glm-5.3-flash": (0.0135, 0.045)}
        bar = market.ask_bar(catalog, ["zai/glm-5.3-flash"])
        self.assertAlmostEqual(bar, 0.0135 * 0.75 + 0.045 * 0.25)

    def test_cheapest_incumbent_wins(self) -> None:
        catalog = {"a/m": (0.02, 0.06), "b/m": (0.01, 0.03)}
        self.assertAlmostEqual(
            market.ask_bar(catalog, ["a/m", "b/m"]), 0.01 * 0.75 + 0.03 * 0.25
        )

    def test_incumbent_absent_from_catalog(self) -> None:
        self.assertIsNone(market.ask_bar({}, ["ghost"]))
        self.assertIsNone(market.ask_bar({"other/m": (0.01, 0.02)}, ["ghost"]))


class FamilyContextBarTests(unittest.TestCase):
    def test_billed_bar_wins_over_asks(self) -> None:
        pricing = {"zai/glm-5.3-flash": {"eff_per_mtok": 0.01, "cache_pct": 50.0}}
        ctx = market.family_context(
            pricing, ["zai/glm-5.3-flash"],
            catalog={"zai/glm-5.3-flash": (0.0135, 0.045)},
        )
        info = ctx["glm-5.3-flash"]
        self.assertEqual(info["bar"], 0.01)
        self.assertEqual(info["bar_source"], "billed")
        self.assertEqual(info["cache_rate"], 0.5)

    def test_unbilled_board_falls_back_to_asks(self) -> None:
        ctx = market.family_context(
            {}, ["ali/deepseek-v4-flash-0731"],
            catalog={"ali/deepseek-v4-flash-0731": (0.0130, 0.0389)},
        )
        info = ctx["deepseek-v4-flash"]
        self.assertAlmostEqual(info["bar"], 0.0130 * 0.75 + 0.0389 * 0.25)
        self.assertEqual(info["bar_source"], "ask")
        self.assertEqual(info["cache_rate"], 0.0)

    def test_unbilled_without_catalog_has_no_bar(self) -> None:
        ctx = market.family_context({}, ["zai/glm-5.3-flash"])
        self.assertIsNone(ctx["glm-5.3-flash"]["bar"])
        self.assertIsNone(ctx["glm-5.3-flash"]["bar_source"])


class DatedOnlyTests(unittest.TestCase):
    def _shortlist(self, catalog, pricing, aliases, proven=None, now=NOW):
        with mock.patch.object(market, "fetch_catalog", return_value=catalog), \
                mock.patch.object(market, "load_aliases", return_value=aliases), \
                mock.patch.object(market, "load_proven", return_value=proven or {}):
            return market.shortlist("key", pricing, root=None, now=now)

    def test_plain_tail_of_dated_family_is_ineligible(self) -> None:
        # a plain flash/pro tail is a different upstream model, not a
        # cheaper substitute of the dated snapshot
        self.assertFalse(market.dated_eligible("ocg/deepseek-v4-flash"))
        self.assertFalse(market.dated_eligible("cp/cline-pass/deepseek-v4-flash"))
        self.assertFalse(market.dated_eligible("cmc/deepseek/deepseek-v4-pro"))

    def test_dated_snapshot_of_dated_family_is_eligible(self) -> None:
        self.assertTrue(market.dated_eligible("ali/deepseek-v4-flash-0731"))
        self.assertTrue(market.dated_eligible("ali/deepseek-v4-pro-0813"))

    def test_undated_family_is_unaffected(self) -> None:
        # glm-5.3-flash has no dated alias -> plain tail stays eligible
        self.assertTrue(market.dated_eligible("zai/glm-5.3-flash"))
        self.assertTrue(market.dated_eligible("cbcn/glm-5.3-flash"))
        self.assertTrue(market.dated_eligible("ali/qwen3.8-max"))

    def test_rank_family_drops_plain_tail(self) -> None:
        ctx = {"cache_rate": 0.0, "w_in": 0.75, "w_out": 0.25}
        catalog = {
            "ocg/deepseek-v4-flash": (0.0001, 0.0003),       # plain -> dropped
            "ali/deepseek-v4-flash-0731": (0.0002, 0.0006),  # dated -> kept
        }
        rows = market.rank_family(catalog, "deepseek-v4-flash", ctx, exclude=set())
        self.assertEqual([r["route"] for r in rows], ["ali/deepseek-v4-flash-0731"])

    def test_shortlist_bars_plain_tail_even_when_cheapest(self) -> None:
        # incumbent bar 0.010; the plain flash routes are cheapest but not
        # valid candidates, and the dated alias is the board seat (excluded),
        # so nothing shortlists — isolates the dated-only gate.
        pricing = {"routes": {"ali/deepseek-v4-flash-0731": {
            "eff_per_mtok": 0.010, "cache_pct": 0.0, "tok_in": 750, "tok_out": 250,
        }}}
        catalog = {
            "ocg/deepseek-v4-flash": (0.0001, 0.0003),              # plain -> barred
            "cp/cline-pass/deepseek-v4-flash": (0.0002, 0.0004),    # plain -> barred
            "ali/deepseek-v4-flash-0731": (0.050, 0.050),           # board -> excluded
        }
        groups = self._shortlist(
            catalog, pricing, aliases=["ali/deepseek-v4-flash-0731"],
        )
        self.assertEqual(groups, [])

    def test_shortlist_admits_dated_snapshot(self) -> None:
        # the dated sibling undercuts the bar -> it shortlists
        pricing = {"routes": {"ocg/deepseek-v4-pro": {
            "eff_per_mtok": 0.10, "cache_pct": 0.0, "tok_in": 750, "tok_out": 250,
        }}}
        catalog = {
            "ali/deepseek-v4-pro-0813": (0.0488, 0.1465),  # predicted 0.0732 < 0.10
        }
        groups = self._shortlist(catalog, pricing, aliases=["ocg/deepseek-v4-pro"])
        self.assertEqual(
            groups, [{"model": "deepseek-v4-pro", "routes": ["ali/deepseek-v4-pro-0813"]}],
        )


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
        proven = {"cb/qwen3.8-max": {
            "last_probe": (NOW - timedelta(days=3)).isoformat(),
            "statuses": {"core": "pass", "cache": "pass"},
        }}
        groups = self._shortlist(proven=proven)
        self.assertEqual(groups[0]["routes"], ["cx/qwen3.8-max"])

    def test_proven_after_ttl_is_picked_again(self) -> None:
        proven = {"cb/qwen3.8-max": {"last_probe": (NOW - timedelta(days=8)).isoformat()}}
        groups = self._shortlist(proven=proven)
        self.assertIn("cb/qwen3.8-max", groups[0]["routes"])

    def test_repricing_does_not_lift_ttl(self) -> None:
        # absolute TTL: same proven entry, cheaper asks in the catalog
        proven = {"cb/qwen3.8-max": {
            "last_probe": (NOW - timedelta(days=1)).isoformat(),
            "statuses": {"core": "pass", "cache": "pass"},
        }}
        catalog = dict(CATALOG)
        catalog["cb/qwen3.8-max"] = (0.0001, 0.0001)  # dramatic price drop
        groups = self._shortlist(catalog=catalog, proven=proven)
        self.assertNotIn("cb/qwen3.8-max", groups[0]["routes"])

    def test_failed_probe_reprobes_when_cheap_even_at_zero_cache(self) -> None:
        # owner rule 2026-09-04: a cache/core failure must not bury a
        # route whose WORST-case ask (zero cache) still beats the bar —
        # worst case 0.001*0.75 + 0.003*0.25 = 0.0015 < bar 0.005
        proven = {"cb/qwen3.8-max": {
            "last_probe": (NOW - timedelta(days=1)).isoformat(),
            "statuses": {"core": "fail", "cache": "skipped"},
        }}
        groups = self._shortlist(proven=proven)
        self.assertEqual(groups[0]["routes"], ["cb/qwen3.8-max", "cx/qwen3.8-max"])

    def test_failed_probe_stays_parked_when_zero_cache_still_dear(self) -> None:
        # failure parked, and even at ZERO cache it cannot beat the bar:
        # predicted 0.002125 < 0.005 but worst case 0.0055 >= 0.005
        proven = {"cb/qwen3.8-max": {
            "last_probe": (NOW - timedelta(days=1)).isoformat(),
            "statuses": {"core": "pass", "cache": "fail"},
        }}
        catalog = dict(CATALOG)
        catalog["cb/qwen3.8-max"] = (0.006, 0.004)
        groups = self._shortlist(catalog=catalog, proven=proven)
        self.assertEqual(groups[0]["routes"], ["cx/qwen3.8-max"])

    def test_passed_probe_stays_parked_even_when_worst_case_cheap(self) -> None:
        # conclusively GOOD probe earns the full freeze — no re-probe
        # spam, even though its worst-case ask would beat the bar
        proven = {"cb/qwen3.8-max": {
            "last_probe": (NOW - timedelta(days=1)).isoformat(),
            "statuses": {"core": "pass", "cache": "pass"},
        }}
        groups = self._shortlist(proven=proven)
        self.assertEqual(groups[0]["routes"], ["cx/qwen3.8-max"])

    def test_flash_variant_stays_out_of_plain_family(self) -> None:
        # glm-5.3 board; the flash sibling is its OWN family and does not
        # compete against the plain glm-5.3 bar
        pricing = {"routes": {"cbcn/glm-5.3": {
            "eff_per_mtok": 0.02, "cache_pct": 50.0, "tok_in": 750, "tok_out": 250,
        }}}
        catalog = {"cbcn/glm-5.3-flash": (0.004, 0.02)}  # would be cheaper, wrong family
        groups = self._shortlist(aliases=["cbcn/glm-5.3"], pricing=pricing, catalog=catalog)
        self.assertEqual(groups, [])

    def test_dated_snapshot_shortlists_into_family(self) -> None:
        # the 0731 snapshot ranks against the deepseek-v4-flash bar
        pricing = {"routes": {"ocg/deepseek-v4-flash": {
            "eff_per_mtok": 0.0107, "cache_pct": 91.0, "tok_in": 900, "tok_out": 100,
        }}}
        catalog = {"ali/deepseek-v4-flash-0731": (0.0130, 0.0389)}
        # predicted 0.0130*0.09*0.9 + 0.0389*0.1 = 0.00494 < 0.0107
        groups = self._shortlist(
            aliases=["ocg/deepseek-v4-flash"], pricing=pricing, catalog=catalog
        )
        self.assertEqual(
            groups,
            [{"model": "deepseek-v4-flash", "routes": ["ali/deepseek-v4-flash-0731"]}],
        )

    def test_family_without_billed_incumbent_is_skipped(self) -> None:
        # no billed usage AND the incumbent is absent from the catalog:
        # there is no bar to beat
        pricing = {"routes": {"zai/glm-5.3": {"eff_per_mtok": None}}}
        groups = self._shortlist(
            aliases=["zai/glm-5.3"], pricing=pricing,
            catalog={"cb/glm-5.3": (0.001, 0.001)},
        )
        self.assertEqual(groups, [])

    def test_unbilled_incumbent_shortlists_via_ask_bar(self) -> None:
        # fresh board route with no billing history: the bar comes from the
        # incumbent's own asks (cache 0), cheaper variants still get probed
        catalog = {
            "zai/glm-5.3-flash": (0.0135, 0.045),       # ask bar 0.021375
            "cbcn/glm-5.3-flash": (0.0045, 0.015),      # 0.007125 -> cheaper
            "cp/zai/glm-5.3-flash": (0.01485, 0.0495),  # 0.023513 -> not cheaper
        }
        groups = self._shortlist(
            aliases=["zai/glm-5.3-flash"], pricing={"routes": {}}, catalog=catalog,
        )
        self.assertEqual(
            groups, [{"model": "glm-5.3-flash", "routes": ["cbcn/glm-5.3-flash"]}]
        )

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

    def test_transient_error_records_status_without_ttl_stamp(self) -> None:
        # one-off HTTP 5xx must not freeze a recovering route for a week
        run_payload = {
            "started_at": "2026-08-28T06:00:00+00:00",
            "cells": [
                {"alias": "cb/m", "check_id": "core", "status": "error",
                 "candidate": True},
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            market.record_proven(run_payload, root=Path(tmp))
            proven = market.load_proven(Path(tmp))
        self.assertEqual(proven["cb/m"]["statuses"], {"core": "error"})
        self.assertNotIn("last_probe", proven["cb/m"])
        self.assertFalse(market.proven_recent(proven, "cb/m", NOW))

    def test_fail_and_pass_still_stamp_ttl(self) -> None:
        # the absolute no-reprobe rule for genuine fails stays untouched
        run_payload = {
            "started_at": "2026-08-28T06:00:00+00:00",
            "cells": [
                {"alias": "cb/m", "check_id": "core", "status": "fail",
                 "candidate": True},
                {"alias": "cx/m", "check_id": "core", "status": "pass",
                 "candidate": True},
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            market.record_proven(run_payload, root=Path(tmp))
            proven = market.load_proven(Path(tmp))
        self.assertEqual(proven["cb/m"]["last_probe"], "2026-08-28T06:00:00+00:00")
        self.assertEqual(proven["cx/m"]["last_probe"], "2026-08-28T06:00:00+00:00")

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

    def test_dry_run_marks_ask_based_bar(self) -> None:
        catalog = {
            "zai/glm-5.3-flash": (0.0135, 0.045),
            "cbcn/glm-5.3-flash": (0.0045, 0.015),
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").mkdir()
            (root / "data" / "pricing.json").write_text(json.dumps({"routes": {}}))
            buf = io.StringIO()
            with mock.patch.object(market, "fetch_catalog", return_value=catalog), \
                    mock.patch.object(market, "load_aliases",
                                      return_value=["zai/glm-5.3-flash"]), \
                    mock.patch.dict(os.environ, {"INFERHUB_API_KEY": "k"}), \
                    redirect_stdout(buf):
                code = market.main(["--dry-run"], root=root)
        out = buf.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("ask-based", out)
        self.assertIn("cbcn/glm-5.3-flash", out)
        self.assertIn("SHORTLIST", out)
        self.assertIn("shortlist total: 1", out)

    def test_dry_run_proven_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root_with_pricing(tmp)
            # last_probe must be relative to the real clock: the TTL check
            # inside market.main() uses datetime.now(), not this module's
            # frozen NOW — a fixture stamped with NOW expired on 09-04.
            fresh = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
            (root / "data" / "proven.json").write_text(json.dumps(
                {"cb/qwen3.8-max": {
                    "last_probe": fresh,
                    "statuses": {"core": "pass", "cache": "pass"},
                }}))
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


class ClassifyLawTest(unittest.TestCase):
    """Direct coverage of _classify — the ONE copy of the parking law.

    _pick and main's display both consume it; these tests pin each branch
    so the printout can never drift from the shortlist (review A5/C4).
    """

    ROUTE = "cb/glm-5.3-flash"
    NOW = datetime(2026, 9, 5, tzinfo=timezone.utc)

    @staticmethod
    def _row(ask_in: float, ask_out: float, predicted: float) -> dict:
        return {"route": ClassifyLawTest.ROUTE, "ask_in": ask_in,
                "ask_out": ask_out, "predicted": predicted}

    def _proven(self, statuses: dict) -> dict:
        fresh = (self.NOW - timedelta(days=1)).isoformat()
        return {self.ROUTE: {"last_probe": fresh, "statuses": statuses}}

    def test_no_bar_skips(self) -> None:
        picked, why = market._classify(self._row(0.004, 0.004, 0.0035),
                                       None, {}, now=self.NOW)
        self.assertFalse(picked)
        self.assertEqual(why, "skip — no incumbent bar")

    def test_not_cheaper_skips(self) -> None:
        picked, why = market._classify(self._row(0.02, 0.02, 0.02),
                                       0.005, {}, now=self.NOW)
        self.assertFalse(picked)
        self.assertEqual(why, "skip — not cheaper")

    def test_parked_pass_stays_parked(self) -> None:
        picked, why = market._classify(
            self._row(0.004, 0.004, 0.0035), 0.005,
            self._proven({"core": "pass", "cache": "pass"}), now=self.NOW)
        self.assertFalse(picked)
        self.assertEqual(why, "skip — proven <7d")

    def test_parked_fail_not_cheap_even_at_zero_cache_stays_parked(self) -> None:
        # ask 0.006/0.006: predicted 0.00375 < bar 0.005, but the zero-cache
        # worst case is 0.006 — the advantage needs caching to survive.
        row = self._row(0.006, 0.006, 0.00375)
        picked, why = market._classify(row, 0.005,
                                       self._proven({"core": "fail"}),
                                       w_in=0.75, w_out=0.25, now=self.NOW)
        self.assertFalse(picked)
        self.assertEqual(why, "skip — proven <7d (failed, not cheap even at 0% cache)")

    def test_parked_fail_cheap_even_at_zero_cache_reenters(self) -> None:
        # ask 0.004/0.004: worst case 0.004 < bar 0.005 — no cache problem
        # could explain the gap away, so the parked fail must re-probe.
        row = self._row(0.004, 0.004, 0.0035)
        picked, why = market._classify(row, 0.005,
                                       self._proven({"core": "fail"}),
                                       w_in=0.75, w_out=0.25, now=self.NOW)
        self.assertTrue(picked)
        self.assertEqual(why, "SHORTLIST (worst-case re-probe — failed probe, cheap even at 0% cache)")

    def test_unproven_route_is_a_plain_shortlist(self) -> None:
        picked, why = market._classify(self._row(0.004, 0.004, 0.0035),
                                       0.005, {}, now=self.NOW)
        self.assertTrue(picked)
        self.assertEqual(why, "SHORTLIST")


class DryRunTopNTest(unittest.TestCase):
    """Routes ranked past TOP_N must print the cutoff marker, not a law one."""

    OVERFLOW_CATALOG = {
        "cb/qwen3.8-max": (0.001, 0.003),     # predicted 0.00094
        "cx/qwen3.8-max": (0.002, 0.004),     # predicted 0.00138
        "cz2/qwen3.8-max": (0.0025, 0.005),   # predicted 0.00219 — third
        "ali/qwen3.8-max": (0.0001, 0.0001),  # board alias -> excluded
    }

    def test_third_route_prints_top_n_cutoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").mkdir()
            (root / "data" / "pricing.json").write_text(json.dumps(PRICING))
            buf = io.StringIO()
            with mock.patch.object(market, "fetch_catalog",
                                   return_value=self.OVERFLOW_CATALOG), \
                    mock.patch.object(market, "load_aliases", return_value=ALIASES), \
                    mock.patch.dict(os.environ, {"INFERHUB_API_KEY": "k"}), \
                    redirect_stdout(buf):
                code = market.main(["--dry-run"], root=root)
        self.assertEqual(code, 0)
        out = buf.getvalue()
        self.assertEqual(out.count("SHORTLIST"), 2)  # TOP_N
        self.assertIn("skip — beyond top-N", out)


if __name__ == "__main__":
    unittest.main()
