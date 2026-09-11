"""Tests for probe/official_compare.py - official-price comparison math."""

import unittest
from typing import ClassVar
from unittest import mock

from probe import pricing
from probe.official_compare import (
    _crown_regrets,
    blended_eff,
    cache_rule_stats,
    comparison_rows,
    drift_flag,
    hit_series,
    inferhub_eff,
    official_eff,
    projection_gate,
    projection_hit,
)

# Real qwen3.8-max window (2026-08-30 snapshot): 684.6M in / 10.4M out,
# 75.8% hit, latest ask 0.14/0.42, measured blended eff 0.0497 $/M.
QWEN = {
    "ask_in": 0.14, "ask_out": 0.42, "cache_pct": 75.8, "reqs": 9455,
    "tok_in": 684_625_299, "tok_out": 10_439_079, "cost_usdc": "18.696416",
}
QWEN_OFFICIAL = {"official_in": 2.0, "official_out": 6.0, "supports_cache": True}


def _with_cached(stats: dict) -> dict:
    stats = dict(stats)
    stats["cached"] = round(stats["tok_in"] * stats["cache_pct"] / 100)
    return stats


class BlendedEffTest(unittest.TestCase):
    def test_matches_measured_qwen_billed_eff(self):
        stats = _with_cached(QWEN)
        eff = inferhub_eff(stats)
        self.assertIsNotNone(eff)
        self.assertAlmostEqual(eff, 0.0497, delta=0.001)

    def test_zero_traffic_gives_none(self):
        self.assertIsNone(blended_eff(0, 0, 0.5, 1.0, 0.1, 1.0))

    def test_full_hit_bills_input_at_cache_rate(self):
        # 1M in all cached + 0 out: cost = 0.1 * ask_in
        eff = blended_eff(1_000_000, 0, 1.0, 0.14, 0.014, 0.42)
        self.assertAlmostEqual(eff, 0.014, delta=1e-9)

    def test_cache_pct_fallback_when_raw_count_missing(self):
        # pricing.json route entries carry cache_pct, not "cached"
        stats = {"tok_in": 1_000_000, "tok_out": 0, "cache_pct": 75.8,
                 "ask_in": 0.14, "ask_out": 0.42}
        eff = inferhub_eff(stats)
        expected = 0.758 * 0.014 + 0.242 * 0.14
        self.assertIsNotNone(eff)
        self.assertAlmostEqual(eff, expected, delta=1e-9)


class OfficialEffTest(unittest.TestCase):
    def test_qwen_official_ratio_matches_hand_calc(self):
        stats = _with_cached(QWEN)
        ih = inferhub_eff(stats)
        off = official_eff(stats, QWEN_OFFICIAL)
        # hand calc: (684.6*0.6356 + 10.4*6)/695 = 0.7159
        self.assertAlmostEqual(off, 0.7159, delta=0.002)
        self.assertAlmostEqual(off / ih, 14.3, delta=0.3)

    def test_no_cache_support_means_no_hit_discount(self):
        stats = _with_cached(QWEN)
        model = dict(QWEN_OFFICIAL, supports_cache=False)
        discounted = official_eff(stats, QWEN_OFFICIAL)
        full = official_eff(stats, model)
        self.assertGreater(full, discounted)


class DriftTest(unittest.TestCase):
    def test_measured_rule_at_10pct_does_not_flag(self):
        self.assertFalse(drift_flag({"hit_ask_ratio": 0.1}))

    def test_rule_change_flags(self):
        self.assertTrue(drift_flag({"hit_ask_ratio": 0.13}))

    def test_unchecked_never_flags(self):
        self.assertFalse(drift_flag({}))
        self.assertFalse(drift_flag({"hit_ask_ratio": None}))


def _row(model="ali/qwen3.8-max", ask=0.15, hit_ratio=0.1, cached=1000, prompt=4000, out=100):
    """Row whose cost is exactly reproducible at hit_ask = hit_ratio*ask."""
    hit_ask = hit_ratio * ask
    cost = (prompt - cached) / 1e6 * ask + cached / 1e6 * hit_ask + out / 1e6 * ask * 3
    return {
        "model": model, "prompt_tokens": prompt, "completion_tokens": out,
        "cached_tokens": cached, "ask_input_per_mtok": str(ask),
        "ask_output_per_mtok": str(ask * 3), "cost_consumer_usdc": f"{cost:.8f}",
    }


class CacheRuleStatsTest(unittest.TestCase):
    def test_exact_rows_solve_the_rule(self):
        rows = [_row() for _ in range(6)]
        self.assertAlmostEqual(cache_rule_stats(rows, "ali/qwen3.8-max"), 0.1, delta=1e-6)

    def test_other_models_ignored(self):
        rows = [_row()] + [_row(model="zai/glm-5.3-flash") for _ in range(6)]
        self.assertIsNone(cache_rule_stats(rows, "ali/qwen3.8-max"))

    def test_thin_window_returns_none(self):
        self.assertIsNone(cache_rule_stats([_row() for _ in range(4)], "ali/qwen3.8-max"))

    def test_rows_without_cached_tokens_are_skipped(self):
        rows = [_row(cached=0) for _ in range(6)]
        self.assertIsNone(cache_rule_stats(rows, "ali/qwen3.8-max"))


CATALOG = {
    "cache_rate": 0.1,
    "models": {
        "ali/qwen3.8-max": QWEN_OFFICIAL,
        "zai/glm-5.3-flash": {"official_in": 0.15, "official_out": 0.5, "supports_cache": True},
    },
}


def _snapshot(hit: float, reqs: int = 200) -> dict:
    """A pricing-route entry whose window hit rate is `hit`."""
    tok_in = 1_000_000
    return {
        "ask_in": 0.14, "ask_out": 0.42, "cache_pct": hit * 100,
        "reqs": reqs, "tok_in": tok_in, "tok_out": 100_000,
        "cached": round(tok_in * hit),
    }


def _dated(points: list[tuple[str, float]], route: str = "ali/qwen3.8-max") -> list:
    """Dated payloads shaped like rundata.load_dated_pricing output."""
    return [
        (day, {"routes": {route: _snapshot(hit)}})
        for day, hit in points
    ]


class ProjectionHitTest(unittest.TestCase):
    """projection_hit: EWMA over snapshots, shrinkage + flag when thin."""

    def test_ewma_weights_recent_snapshots(self):
        dated = _dated([("2026-08-28", 0.0), ("2026-08-29", 1.0)])
        stats = _snapshot(1.0)  # live window: full hit
        hit, conf = projection_hit(dated, "ali/qwen3.8-max", stats, {})
        # seed 0.0 -> 0.4*1.0 + 0.6*0.0 = 0.4 -> 0.4*1.0 + 0.6*0.4 = 0.64
        self.assertAlmostEqual(hit, 0.64, delta=1e-9)
        self.assertEqual(conf, "ok")  # reqs 200 >= MIN_CONF_REQS

    def test_thin_route_shrinks_toward_fleet_prior(self):
        stats = _snapshot(0.0, reqs=10)  # thin: few requests, zero hits
        routes = {
            "r/thin": stats,
            "p/one": _snapshot(0.5), "p/two": _snapshot(0.6), "p/three": _snapshot(0.7),
        }
        hit, conf = projection_hit([], "r/thin", stats, routes)
        # (10*0.0 + 20*0.6) / 30 - prior is the fleet median 0.6
        self.assertAlmostEqual(hit, 0.4, delta=1e-9)
        self.assertEqual(conf, "low")

    def test_no_fleet_prior_leaves_thin_route_flagged_unshrunk(self):
        stats = _snapshot(0.0, reqs=10)
        hit, conf = projection_hit([], "r/thin", stats, {"r/thin": stats})
        self.assertEqual(hit, 0.0)  # no confident peers - raw EWMA stands, flagged
        self.assertEqual(conf, "low")

    def test_no_hit_evidence_gives_none(self):
        stats = {"tok_in": 0, "tok_out": 0, "reqs": 0}
        self.assertEqual(projection_hit([], "x", stats, {}), (None, "low"))


class HitSeriesHygieneTest(unittest.TestCase):
    """hit_series drops thin snapshots: same MIN_CONF_REQS law the live
    window gets. A 4-req 29% day and a 1-req 0% day must not poison the
    EWMA that prices every later confident snapshot (cbcn, 2026-09-07)."""

    def test_thin_snapshots_excluded(self):
        dated = [
            ("2026-08-28", {"routes": {"r/x": _snapshot(0.286, reqs=4)}}),
            ("2026-09-05", {"routes": {"r/x": _snapshot(0.0, reqs=1)}}),
            ("2026-09-06", {"routes": {"r/x": _snapshot(0.949, reqs=237)}}),
        ]
        self.assertEqual(hit_series(dated, "r/x"), [0.949])

    def test_projection_ignores_poison(self):
        dated = [
            ("2026-08-28", {"routes": {"r/x": _snapshot(0.286, reqs=4)}}),
            ("2026-09-05", {"routes": {"r/x": _snapshot(0.0, reqs=1)}}),
        ]
        stats = _snapshot(0.946, reqs=4709)
        hit, conf = projection_hit(dated, "r/x", stats, {})
        self.assertEqual(conf, "ok")
        self.assertGreater(hit, 0.93)  # was 0.779 with the poison in


class ProjectionGateTest(unittest.TestCase):
    """projection_gate: the top-1 CROWN backtest, not a whole-ranking score.

    The gate certifies what the BOARD shows: per transition it crowns the
    lowest projected $/M route and asks whether that crown's realized cost
    landed within GATE_TOL of the true cheapest realized route. These
    fixtures carry distinct projections so every transition has a crown.
    """

    ROUTES: ClassVar[tuple] = ("r/a", "r/b", "r/c", "r/d")
    MULTS: ClassVar[tuple] = (1.0, 2.0, 3.0, 4.0)

    @classmethod
    def _route_stats(cls, mult: float) -> dict:
        """One route's stats; its projection scales with `mult`."""
        return {
            "ask_in": 0.14 * mult, "ask_out": 0.42 * mult, "cache_pct": 50.0,
            "reqs": 200, "tok_in": 1_000_000, "tok_out": 100_000,
            "cached": 500_000,
        }

    @classmethod
    def _dated(cls, days: int, realized_scale: float = 1.0,
               reversed_days=frozenset(), routes=None) -> list:
        """Dated snapshots over distinct routes; the newer side stamps realized.

        Each route carries a distinct projection, so every transition has a
        crown - the lowest projected $/M route, r/a. A snapshot whose index is
        in `reversed_days` stamps its realized eff in the REVERSED route order,
        so the crowned route realizes the priciest cost - the gate must fail
        on that.
        """
        routes = cls.ROUTES if routes is None else routes
        mults = cls.MULTS
        out = []
        for i in range(days):
            day_routes = {}
            for j, route in enumerate(routes):
                st = cls._route_stats(mults[j % len(mults)])
                if i > 0:  # realized eff only matters on the newer side of a pair
                    k = len(routes) - 1 - j if i in reversed_days else j
                    st["eff_per_mtok"] = round(
                        inferhub_eff(cls._route_stats(mults[k % len(mults)]))
                        * realized_scale, 6)
                day_routes[route] = st
            out.append((f"2026-08-{i + 1:02d}", {"routes": day_routes}))
        return out

    @classmethod
    def _pair(cls, realized: dict) -> list:
        """One transition: crown r/a (projections r/a<r/b<r/c), explicit realized.

        The realized eff is stamped verbatim so a test can place the crown at a
        chosen distance above the cheapest realized route.
        """
        mults = {"r/a": 1.0, "r/b": 2.0, "r/c": 3.0}
        old = {r: cls._route_stats(m) for r, m in mults.items()}
        new = {r: dict(old[r], eff_per_mtok=v) for r, v in realized.items()}
        return [("2026-08-01", {"routes": old}),
                ("2026-08-02", {"routes": new})]

    def test_passes_when_the_crown_realizes_cheapest(self):
        gate = projection_gate(self._dated(12))
        self.assertEqual(gate["n"], 11)
        self.assertEqual(gate["land"], 11)
        self.assertEqual(gate["share"], 1.0)
        self.assertEqual(gate["tol"], 0.15)
        self.assertEqual(gate["min_n"], 10)
        self.assertTrue(gate["pass"])

    def test_a_uniform_bias_leaves_the_crown_and_its_regret_intact(self):
        """The gate scores the crown's RELATIVE cost: a constant factor cancels."""
        gate = projection_gate(self._dated(12, realized_scale=2.0))
        self.assertEqual(gate["land"], 11)
        self.assertTrue(gate["pass"])

    def test_a_reversed_order_never_lands(self):
        """The crowned route realizes the priciest cost - the gate keeps teeth."""
        gate = projection_gate(
            self._dated(12, reversed_days=frozenset(range(1, 12))))
        self.assertEqual(gate["n"], 11)
        self.assertEqual(gate["land"], 0)
        self.assertEqual(gate["share"], 0.0)
        self.assertFalse(gate["pass"])

    def test_fails_when_share_falls_below_the_bar(self):
        gate = projection_gate(
            self._dated(12, reversed_days=frozenset({1, 2, 3})))
        self.assertEqual(gate["n"], 11)
        self.assertEqual(gate["land"], 8)
        self.assertAlmostEqual(gate["share"], 0.727, places=3)
        self.assertFalse(gate["pass"])

    def test_one_bad_crown_still_clears_the_bar(self):
        gate = projection_gate(self._dated(12, reversed_days=frozenset({1})))
        self.assertEqual(gate["land"], 10)
        self.assertAlmostEqual(gate["share"], 0.909, places=3)
        self.assertTrue(gate["pass"])

    def test_thin_history_never_passes(self):
        gate = projection_gate(self._dated(4))
        self.assertLess(gate["n"], 10)
        self.assertFalse(gate["pass"])

    def test_fewer_than_three_comparable_routes_never_counts(self):
        """Degenerate transitions are skipped, not scored: n stays 0."""
        gate = projection_gate(self._dated(12, routes=("r/a",)))
        self.assertEqual(gate["n"], 0)
        self.assertFalse(gate["pass"])

    def test_crown_just_over_tolerance_does_not_land(self):
        """Teeth: a crown 16% over the cheapest realized route MUST fail."""
        gate = projection_gate(
            self._pair({"r/a": 1.16, "r/b": 1.0, "r/c": 1.2}))
        self.assertEqual(gate["n"], 1)
        self.assertEqual(gate["land"], 0)
        self.assertFalse(gate["pass"])

    def test_a_crown_at_twice_the_cheapest_never_lands(self):
        """Teeth, far tail: a crown at 2x the true cheapest MUST not land.

        `land` is the load-bearing assertion - `pass` is false here for two
        independent reasons (the missed landing and n=1 < GATE_MIN_N), so a
        loud teeth test needs the aggregate too:
        `test_a_reversed_order_never_lands` drives 11 transitions with the
        crown priciest and gets land=0, share=0.0, pass=False.
        """
        gate = projection_gate(
            self._pair({"r/a": 2.0, "r/b": 1.0, "r/c": 1.2}))
        self.assertEqual(gate["n"], 1)
        self.assertEqual(gate["land"], 0)
        self.assertFalse(gate["pass"])

    def test_crown_just_under_tolerance_lands(self):
        """The 15% tolerance is inclusive at the boundary: 14% lands."""
        gate = projection_gate(
            self._pair({"r/a": 1.14, "r/b": 1.0, "r/c": 1.2}))
        self.assertEqual(gate["n"], 1)
        self.assertEqual(gate["land"], 1)

    def test_empty_or_malformed_history_is_an_honest_fail(self):
        self.assertFalse(projection_gate([])["pass"])
        self.assertFalse(projection_gate({})["pass"])
        self.assertFalse(projection_gate([("d", "not-a-payload")])["pass"])

class CrownLadderTest(unittest.TestCase):
    """The owner's 15% ruling, reproduced on the committed crown history.

    Frozen to the snapshots through 2026-09-10 - the 14 scorable transitions
    the owner measured. The crown regret is bimodal: 11 transitions crown the
    true cheapest (0% regret), then 10.8%, 55.7% and 113.2%. That ladder is
    exactly why the tolerance is 15%: 0% fails (11/14), 15% passes (12/14), and
    the tail would need 60%/120% to certify a projection that crowns a
    113%-over route. Asserting the frozen prefix keeps the test stable as the
    sweep appends new snapshots.
    """

    @staticmethod
    def _through(day: str) -> list:
        return [pair for pair in pricing.dated_snapshots() if pair[0] <= day]

    def test_the_ladder_reproduces_the_owners_evidence(self):
        regrets = _crown_regrets(self._through("2026-09-10"))
        self.assertEqual(len(regrets), 14)
        ladder = {
            tol: sum(1 for r in regrets if r <= tol)
            for tol in (0.0, 0.15, 0.60, 1.20)
        }
        self.assertEqual(ladder, {0.0: 11, 0.15: 12, 0.60: 13, 1.20: 14})

    def test_gate_passes_at_the_fifteen_percent_ruling(self):
        gate = projection_gate(self._through("2026-09-10"))
        self.assertEqual(gate["n"], 14)
        self.assertEqual(gate["land"], 12)
        self.assertAlmostEqual(gate["share"], 0.857, places=3)
        self.assertEqual(gate["tol"], 0.15)
        self.assertTrue(gate["pass"])



class ComparisonRowsTest(unittest.TestCase):
    def test_rows_ratios_and_notes(self):
        pricing = {"routes": {
            "ali/qwen3.8-max": _with_cached(QWEN),
            "zai/glm-5.3-flash": dict(_with_cached(QWEN), reqs=1),  # thin
            "ocg/mystery-model": _with_cached(QWEN),  # unknown family
        }}
        rows = {r["route"]: r for r in comparison_rows(pricing, CATALOG)}
        q = rows["ali/qwen3.8-max"]
        self.assertIsNone(q["note"])
        self.assertAlmostEqual(q["ratio"], 14.3, delta=0.3)
        self.assertAlmostEqual(q["ih_eff"], 0.0497, delta=0.001)
        thin = rows["zai/glm-5.3-flash"]
        self.assertIn("thin", thin["note"])
        self.assertIsNone(thin["ih_eff"])
        mystery = rows["ocg/mystery-model"]
        self.assertIn("not in catalog", mystery["note"])

    def test_dated_alias_matches_family(self):
        # ali/deepseek-v4-flash-0731 -> family deepseek-v4-flash -> catalog plain tail
        catalog = {"models": {
            "ali/deepseek-v4-flash": {"official_in": 0.22, "official_out": 0.66, "supports_cache": True},
        }}
        pricing = {"routes": {"ali/deepseek-v4-flash-0731": _with_cached(QWEN)}}
        rows = comparison_rows(pricing, catalog)
        self.assertIsNone(rows[0]["note"])
        self.assertIsNotNone(rows[0]["off_eff"])

    def test_zero_traffic_routes_omitted(self):
        pricing = {"routes": {"ocg/mystery-model": dict(_with_cached(QWEN), reqs=0)}}
        self.assertEqual(comparison_rows(pricing, CATALOG), [])

    def test_empty_inputs_do_not_crash(self):
        self.assertEqual(comparison_rows({}, {}), [])

    def test_dated_smooths_forward_columns_and_flags_confidence(self):
        pricing = {"routes": {"ali/qwen3.8-max": _with_cached(QWEN)}}
        # dated history says the route cached 0% before today's 75.8% window
        dated = _dated([("2026-08-28", 0.0), ("2026-08-29", 0.0), ("2026-08-30", 0.0)])
        smooth = {r["route"]: r for r in comparison_rows(pricing, CATALOG, dated)}
        window = {r["route"]: r for r in comparison_rows(pricing, CATALOG)}
        # smoothed hit (0.303) is below the window's 75.8% -> projection is dearer
        self.assertGreater(smooth["ali/qwen3.8-max"]["ih_eff"], window["ali/qwen3.8-max"]["ih_eff"])
        self.assertEqual(smooth["ali/qwen3.8-max"]["hit_conf"], "ok")  # 9455 reqs
        self.assertEqual(window["ali/qwen3.8-max"]["hit_conf"], "ok")


class BoardIqSortTest(unittest.TestCase):
    """Board rows sort by realized $/M ascending (IA law: the board answers
    "what is this costing me"); ties break by IQ per $ descending; routes
    without an eff figure sink last."""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        import sys as _sys

        from probe.registry import repo_root

        _sys.path.insert(0, str(repo_root() / "site"))
        import rundata

        cls.rundata = rundata
        path = repo_root() / "site" / "generate.py"
        spec = importlib.util.spec_from_file_location("watch_generate_sort", path)
        assert spec and spec.loader
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)

    def _section(self, routes_order: list[str]) -> str:
        rows = {
            "ali/kimi-k3": {"ask_in": 0.195, "ask_out": 0.975, "eff_per_mtok": 0.28,
                            "reqs": 10, "source": "usage-logs"},
            "zai/glm-5.3-flash": {"ask_in": 0.015, "ask_out": 0.05, "eff_per_mtok": 0.0113,
                                  "reqs": 10, "source": "usage-logs"},
            "ocg/unmapped": {"ask_in": 0.01, "ask_out": 0.02, "eff_per_mtok": 0.015,
                             "reqs": 10, "source": "usage-logs"},
        }
        payload = {"range": "30d", "requests_scanned": 100,
                   "routes": {r: rows[r] for r in routes_order}}
        intel = {"models": {"glm-5-3-flash": {"iq": 57.5},
                            "kimi-k3": {"iq": 59.7}}}
        rd, mod = self.rundata, self.mod
        input_rows = [{"route": r, **payload["routes"][r]} for r in routes_order]
        with mock.patch.object(rd, "pricing_rows", return_value=input_rows), \
            mock.patch.object(rd, "load_dated_pricing", return_value=[]), \
            mock.patch.object(rd, "load_intelligence", return_value=intel), \
            mock.patch.object(rd, "ask_series", return_value=[]), \
            mock.patch.object(rd, "load_catalog", return_value={"models": {}}):
            return mod.pricing_section(payload, [])

    def test_sorted_asc_by_realized_cost(self):
        # input order deliberately scrambled. eff: glm 0.0113 < unmapped
        # 0.015 < kimi 0.28 -> that is the row order now; IQ/$ only breaks
        # ties at equal cost.
        out = self._section(["ali/kimi-k3", "ocg/unmapped", "zai/glm-5.3-flash"])
        g, k, u = (out.index(f"<code>{r}</code>")
                   for r in ("zai/glm-5.3-flash", "ali/kimi-k3", "ocg/unmapped"))
        self.assertLess(g, u, "glm ($0.0113) must precede unmapped ($0.015)")
        self.assertLess(u, k, "unmapped ($0.015) must precede kimi ($0.28)")

    def test_equal_cost_ties_break_by_iq_per_dollar(self):
        rows = {
            "ali/tie-low-iq": {"ask_in": 0.01, "ask_out": 0.02, "eff_per_mtok": 0.012,
                             "reqs": 10, "source": "usage-logs"},
            "ali/tie-high-iq": {"ask_in": 0.01, "ask_out": 0.02, "eff_per_mtok": 0.012,
                              "reqs": 10, "source": "usage-logs"},
        }
        payload = {"range": "30d", "requests_scanned": 100,
                   "routes": {r: rows[r] for r in ("ali/tie-low-iq", "ali/tie-high-iq")}}
        intel = {"models": {"tie-low-iq": {"iq": 30.0}, "tie-high-iq": {"iq": 60.0}}}
        rd, mod = self.rundata, self.mod
        input_rows = [{"route": r, **payload["routes"][r]} for r in payload["routes"]]
        with mock.patch.object(rd, "pricing_rows", return_value=input_rows), \
            mock.patch.object(rd, "load_dated_pricing", return_value=[]), \
            mock.patch.object(rd, "load_intelligence", return_value=intel), \
            mock.patch.object(rd, "ask_series", return_value=[]), \
            mock.patch.object(rd, "load_catalog", return_value={"models": {}}):
            out = mod.pricing_section(payload, [])
        lo = out.index("<code>ali/tie-low-iq</code>")
        hi = out.index("<code>ali/tie-high-iq</code>")
        self.assertLess(hi, lo, "at equal cost, higher IQ/$ ranks first")

    def test_routes_without_eff_sink_last(self):
        rows = {
            "a/billed": {"ask_in": 0.01, "ask_out": 0.02, "eff_per_mtok": 0.012,
                         "reqs": 10, "source": "usage-logs"},
            "b/noeff": {"ask_in": 0.01, "ask_out": 0.02, "reqs": 10,
                        "source": "usage-logs"},
        }
        payload = {"range": "30d", "requests_scanned": 100,
                   "routes": {r: rows[r] for r in ("b/noeff", "a/billed")}}
        intel = {"models": {}}
        rd, mod = self.rundata, self.mod
        input_rows = [{"route": r, **payload["routes"][r]} for r in payload["routes"]]
        with mock.patch.object(rd, "pricing_rows", return_value=input_rows), \
            mock.patch.object(rd, "load_dated_pricing", return_value=[]), \
            mock.patch.object(rd, "load_intelligence", return_value=intel), \
            mock.patch.object(rd, "ask_series", return_value=[]), \
            mock.patch.object(rd, "load_catalog", return_value={"models": {}}):
            out = mod.pricing_section(payload, [])
        billed = out.index("<code>a/billed</code>")
        noeff = out.index("<code>b/noeff</code>")
        self.assertLess(billed, noeff, "route without an eff figure sinks last")


class CandMarkRenderTest(BoardIqSortTest):
    """Render-level proof: a billed candidate row carries the cand-mark span.

    Data-layer coverage (test_rundata) keeps the row; this pins that the
    renderer actually emits the marker — deleting the cand variable from
    pricing_section must fail here.
    """

    def test_billed_candidate_row_renders_cand_mark(self):
        rows = {
            "ali/kimi-k3": {"ask_in": 0.195, "ask_out": 0.975, "eff_per_mtok": 0.28,
                            "reqs": 10, "source": "usage-logs"},
            "cb/cand-billed": {"ask_in": 0.01, "ask_out": 0.02, "eff_per_mtok": 0.008,
                               "reqs": 42, "source": "usage-logs",
                               "candidate": True},
        }
        payload = {"range": "30d", "requests_scanned": 100,
                   "routes": {r: rows[r] for r in ("ali/kimi-k3", "cb/cand-billed")}}
        intel = {"models": {"kimi-k3": {"iq": 59.7}}}
        rd, mod = self.rundata, self.mod
        input_rows = [{"route": r, **payload["routes"][r]} for r in payload["routes"]]
        with mock.patch.object(rd, "pricing_rows", return_value=input_rows), \
            mock.patch.object(rd, "load_dated_pricing", return_value=[]), \
            mock.patch.object(rd, "load_intelligence", return_value=intel), \
            mock.patch.object(rd, "ask_series", return_value=[]), \
            mock.patch.object(rd, "load_catalog", return_value={"models": {}}):
            out = mod.pricing_section(payload, [])
        self.assertIn("cand-mark", out)
        self.assertIn("&#9666; cand", out)
        # and the board row carries none
        board_row = out[out.index("<code>ali/kimi-k3</code>"):]
        board_row = board_row[:board_row.index("</tr>")]
        self.assertNotIn("cand-mark", board_row)

    def test_pricing_rows_skip_edge_reqs_but_no_money(self):
        # candidate flagged, reqs > 0, but eff and cost both None: skip
        payload = {"routes": {"c/ghost": {"ask_in": 1.0, "ask_out": 3.0,
                                          "candidate": True, "reqs": 5}}}
        self.assertEqual(self.rundata.pricing_rows(payload), [])

    def test_perf_ttft_tps_render_as_plumbing_cells(self):
        # perf table removed (owner 2026-09-07): ttft p50 / tps mean render as
        # plumbing cells in the pricing row's hidden plumb dl
        rows = {"ali/qwen3.8-max": {"ask_in": 0.014, "ask_out": 0.042,
                                    "eff_per_mtok": 0.0201, "reqs": 12,
                                    "source": "usage-logs"}}
        payload = {"range": "30d", "requests_scanned": 100, "routes": rows,
                   "perf": {"window_hours": 24, "models": {
                       "ali/qwen3.8-max": {"reqs": 100, "ttft_p50_ms": 2200,
                                           "tps_mean": 55.0}}}}
        rd, mod = self.rundata, self.mod
        input_rows = [{"route": r, **payload["routes"][r]} for r in payload["routes"]]
        with mock.patch.object(rd, "pricing_rows", return_value=input_rows), \
            mock.patch.object(rd, "load_dated_pricing", return_value=[]), \
            mock.patch.object(rd, "load_intelligence", return_value={"models": {}}), \
            mock.patch.object(rd, "ask_series", return_value=[]):
            html = mod.pricing_section(payload, [])
        self.assertIn("<dt>ttft p50</dt>", html)
        self.assertIn("<dt>tps mean</dt>", html)
        self.assertIn("2.20s", html)
        self.assertIn("55.0", html)



class ScatterSectionTest(unittest.TestCase):
    """Price-vs-IQ scatter: marginal x, IQ y, usage color, touch tips."""

    @classmethod
    def setUpClass(cls) -> None:
        import importlib.util

        from probe.registry import repo_root
        path = repo_root() / "site" / "generate.py"
        spec = importlib.util.spec_from_file_location("watch_generate_scatter", path)
        assert spec and spec.loader
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)

    INTEL: ClassVar[dict] = {"models": {"glm-5-3-flash": {"iq": 46.2}, "kimi-k3": {"iq": 50.2},
                        "kimi-k2": {"iq": 44.0}}}

    def _payload(self) -> dict:
        return {"range": "30d", "requests_scanned": 100,
                "perf": {"window_hours": 24, "models": {
                    "zai/glm-5.3-flash": {"reqs": 2500},
                    "ali/kimi-k3": {"reqs": 150},
                    "ali/kimi-k2": {"reqs": 3},
                }},
                "routes": {
                    "zai/glm-5.3-flash": {"ask_in": 0.01, "ask_out": 0.03,
                                          "eff_per_mtok": 0.0098,
                                          "marginal_per_mtok": 0.0041,
                                          "reqs": 100, "source": "usage-logs"},
                    "ali/kimi-k3": {"ask_in": 0.02, "ask_out": 0.06,
                                    "eff_per_mtok": 0.019,
                                    "marginal_per_mtok": 0.019,
                                    "reqs": 10, "source": "usage-logs"},
                    "ali/kimi-k2": {"ask_in": 0.01, "ask_out": 0.02,
                                    "eff_per_mtok": 0.008,
                                    "reqs": 10, "source": "usage-logs"},
                    "ocg/unmapped": {"ask_in": 0.01, "ask_out": 0.02,
                                     "eff_per_mtok": 0.015, "marginal_per_mtok": 0.014,
                                     "reqs": 10, "source": "usage-logs"},
                }}

    def _svg(self) -> str:
        return self.mod.scatter_section(self._payload(), self.INTEL)

    def test_every_priced_and_iq_route_renders_a_tipped_circle(self) -> None:
        svg = self._svg()
        for route in ("zai/glm-5.3-flash", "ali/kimi-k3", "ali/kimi-k2"):
            # data-tip precedes aria-label inside the same <circle .../>;
            # the chart renders twice (desktop + mobile SVG, QA M2), so the
            # count is 2 — one circle per rendering
            at = svg.index(f'aria-label="{route}"')
            head = svg[max(0, at - 400):at]
            self.assertEqual(2, svg.count(f'aria-label="{route}"'), route)
            self.assertIn("data-tip=", head, route)
        self.assertNotIn("ocg/unmapped", svg)  # no IQ -> skipped

    def test_marginal_price_is_the_x_basis(self) -> None:
        svg = self._svg()
        self.assertIn("(marginal)", svg)
        self.assertIn("0.0041", svg)  # zai marginal, not eff 0.0098

    def test_eff_fallback_when_no_marginal_sample(self) -> None:
        svg = self._svg()
        self.assertIn("$0.0080 $/M (eff)", svg)  # kimi-k2 has no marginal

    def test_usage_color_binary_in_use(self) -> None:
        # Owner 2026-09-07: "in use" needs meaningful volume — trace-level
        # billed requests (1–99/24h) render gray, teal starts at 100.
        f = self.mod.usage_color
        self.assertEqual("var(--ok)", f(2500))
        self.assertEqual("var(--ok)", f(150))
        self.assertEqual("var(--muted)", f(99))
        self.assertEqual("var(--muted)", f(3))
        self.assertEqual("var(--muted)", f(None))
        self.assertEqual("var(--muted)", f(0))

    def test_usage_radius_scales_with_requests(self) -> None:
        f = self.mod.usage_radius
        self.assertEqual(f(0), f(None))
        self.assertEqual(3.5, f(0))
        self.assertEqual(3.5, f(9, probe_only=True))  # probes don't grow dots
        big = f(8900)
        small = f(457)
        self.assertGreater(big, small)
        self.assertGreater(small, 3.5)
        self.assertLessEqual(big, 9.0)

    def test_probe_only_traffic_is_not_in_use(self) -> None:
        # Owner order 2026-09-07: probes-only routes render amber, not teal.
        f = self.mod.usage_color
        self.assertEqual("var(--warn, #c90)", f(2500, probe_only=True))
        self.assertEqual("var(--warn, #c90)", f(3, probe_only=True))
        # Real traffic with probe_only=False stays teal.
        self.assertEqual("var(--ok)", f(2500, probe_only=False))

    def test_probe_only_dot_renders_amber_with_tip_note(self) -> None:
        runs = [{"started_at": "2026-09-07T09:00:00+00:00",
                 "finished_at": "2026-09-07T09:10:00+00:00",
                 "aliases": ["zai/glm-5.3-flash"]}]
        payload = self._payload()
        # zai's only traffic is inside the sweep window above -> probes only.
        payload["perf"]["models"]["zai/glm-5.3-flash"] = {"reqs": 2500}
        route_row = payload["routes"]["zai/glm-5.3-flash"]
        route_row["marginal_reqs"] = 2
        route_row["marginal_ts"] = ["2026-09-07T09:02:00Z", "2026-09-07T09:08:00Z"]
        route_row["marginal_ts_truncated"] = False
        svg = self.mod.scatter_section(payload, self.INTEL, runs)
        self.assertIn("var(--warn, #c90)", svg)
        self.assertIn("probes only", svg)  # tip note on the dot + legend

    def test_dots_carry_model_name_labels(self) -> None:
        svg = self._svg()
        # chart renders twice (desktop + mobile SVG, QA M2) -> 2x labels
        self.assertEqual(svg.count('class="slabel"'), 6)
        self.assertIn(">zai/glm-5.3-flash</text>", svg)
        self.assertIn(">ali/kimi-k3</text>", svg)

    def test_mobile_svg_keeps_full_route_names(self) -> None:
        # Owner 2026-09-08: mobile labels must not truncate. The longest
        # probed route is 35 chars; the cap must cover it and rendered
        # labels must carry the full name, no ellipsis.
        svg = self._svg()
        self.assertNotIn("\u2026</text>", svg)
        self.assertIn(">zai/glm-5.3-flash</text>", svg)
        self.assertIn(">ali/kimi-k3</text>", svg)

    def test_mobile_css_gives_chart_full_width(self) -> None:
        # Owner 2026-09-08: chart full width on mobile — the mobile media
        # block must break out of main's gutters and lift the 720px cap,
        # while caption + legend keep the gutters (padding follow-up).
        from probe.registry import repo_root

        css = (repo_root() / "site" / "style.css").read_text()
        block = css[css.index("@media (max-width: 720px)", css.index("Price-vs-IQ scatter")):]
        block = block[: block.index(".scatter .sg")]
        self.assertIn("margin-inline: -1rem", block)
        self.assertIn(".scatter svg.mobile { display: block; max-width: none; }", block)
        self.assertIn(".scatter .scatter-legend { margin-inline: 1rem; }", block)
        self.assertIn(".scatter figcaption,", block)

    def test_mobile_nav_menu_full_bleed(self) -> None:
        # Owner 2026-09-08: the floating menu showed a 1rem gap on the
        # left with the layer below visible — the dropdown's containing
        # block is the sticky header (inset 1rem per side), so left:0
        # left that strip open. The menu must bleed to the viewport.
        from probe.registry import repo_root

        css = (repo_root() / "site" / "style.css").read_text()
        block = css[css.index("@media (max-width: 720px)", css.index(".nav-menu summary {", 1010)):]
        block = block[: block.index("}", block.index(".site-nav ul {"))]
        self.assertIn("left: calc(50% - 50vw);", block)
        self.assertIn("right: calc(50% - 50vw);", block)

    def test_svg_axis_title_gone_from_svg_lives_in_legend(self) -> None:
        # Owner 2026-09-08, SECOND order (supersedes the HTML-legend move
        # from earlier the same day): axis names live INSIDE the SVG,
        # beside the axis ends — "AA IQ" on the y-axis top, "marginal
        # $/M" on the x-axis cheap end. Legend carries only the dot key.
        svg = self._svg()
        self.assertIn('class="st saxis" text-anchor="start">AA IQ</text>', svg)
        self.assertIn('class="st saxis" text-anchor="end">marginal $/M</text>', svg)
        self.assertNotIn('class="lg lg-axis"', svg)

    def test_mobile_grid_spans_full_viewbox(self) -> None:
        # Owner 2026-09-08: on mobile the grid must span ~the full SVG
        # width, not 67% — the old 40/104 pads starved the plot. Vertical
        # gridlines must sit within 16px of each viewBox edge.
        import re

        svg = self._svg()
        body = svg[svg.index('viewBox="0 0 440'):]
        body = body[: body.index("</svg>")]
        verts = sorted(
            {
                float(a)
                for a, c in re.findall(
                    r'<line x1="([\d.]+)" y1="[\d.]+" x2="([\d.]+)" y2="[\d.]+" class="sg"/>',
                    body,
                )
                if a == c
            }
        )
        self.assertEqual(5, len(verts))
        self.assertLessEqual(verts[0], 16)
        self.assertGreaterEqual(verts[-1], 424)

    def test_legend_and_caption_present(self) -> None:
        svg = self._svg()
        self.assertIn("scatter-legend", svg)
        self.assertIn("Marginal $/M vs AA IQ", svg)
        self.assertIn("Not plotted: 1", svg)  # ocg/unmapped has no IQ
        self.assertIn("reading guide", svg)

    def test_section_skipped_without_snapshot_or_sparse_points(self) -> None:
        self.assertEqual("", self.mod.scatter_section(None, self.INTEL))
        lone = self._payload()
        lone["routes"] = {"zai/glm-5.3-flash": lone["routes"]["zai/glm-5.3-flash"]}
        self.assertEqual("", self.mod.scatter_section(lone, self.INTEL))

if __name__ == "__main__":
    unittest.main()

