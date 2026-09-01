"""Tests for probe/official_compare.py - official-price comparison math."""

import unittest
from unittest import mock

from probe.official_compare import (
    blended_eff,
    cache_rule_stats,
    comparison_rows,
    drift_flag,
    inferhub_eff,
    official_eff,
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


class OfficialTableRenderTest(unittest.TestCase):
    """official_table() renders rows, projection, drift note; gaps handled."""

    @classmethod
    def setUpClass(cls):
        import importlib.util

        from probe.registry import repo_root

        path = repo_root() / "site" / "generate.py"
        spec = importlib.util.spec_from_file_location("watch_generate_official", path)
        assert spec and spec.loader
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)

    def _pricing(self, **extra):
        return {"range": "30d", "requests_scanned": 100,
                "routes": {"ali/qwen3.8-max": _with_cached(QWEN), **extra}}

    def test_rows_ratio_and_projection_render(self):
        out = self.mod.official_table(self._pricing(), {"models": {"ali/qwen3.8-max": QWEN_OFFICIAL}})
        self.assertIn("ali/qwen3.8-max", out)
        self.assertIn("&times;", out)
        self.assertIn("official costs", out)  # projection line
        self.assertIn("current rates", out)

    def test_gap_row_shows_note(self):
        pricing = self._pricing(**{"ocg/mystery-model": dict(_with_cached(QWEN), reqs=50)})
        out = self.mod.official_table(pricing, {"models": {"ali/qwen3.8-max": QWEN_OFFICIAL}})
        self.assertIn("not in catalog", out)

    def test_drift_note_renders(self):
        flagged = dict(_with_cached(QWEN), hit_ask_ratio=0.13)
        out = self.mod.official_table(
            {"range": "30d", "routes": {"ali/qwen3.8-max": flagged}},
            {"models": {"ali/qwen3.8-max": QWEN_OFFICIAL}},
        )
        self.assertIn("Cache-rule drift", out)

    def test_no_catalog_or_no_quoted_rows_gives_empty(self):
        self.assertEqual(self.mod.official_table(self._pricing(), None), "")
        self.assertEqual(self.mod.official_table(None, {"models": {}}), "")
        thin = self.mod.official_table(
            {"routes": {"zai/glm-5.3-flash": dict(_with_cached(QWEN), reqs=1)}},
            {"models": {"ali/qwen3.8-max": QWEN_OFFICIAL}},
        )
        self.assertEqual(thin, "")


if __name__ == "__main__":
    unittest.main()


class BoardIqSortTest(unittest.TestCase):
    """Board rows sort by IQ per $ descending; unmapped routes sink last."""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        import sys as _sys

        from probe.registry import repo_root

        _sys.path.insert(0, str(repo_root() / "site"))
        import rundata  # noqa: E402

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

    def test_sorted_desc_by_iq_per_dollar_unmapped_last(self):
        # input order deliberately scrambled: kimi first, glm middle, unmapped last
        out = self._section(["ali/kimi-k3", "ocg/unmapped", "zai/glm-5.3-flash"])
        g, k, u = (out.index(f"<code>{r}</code>")
                   for r in ("zai/glm-5.3-flash", "ali/kimi-k3", "ocg/unmapped"))
        self.assertLess(g, k, "glm (5088 IQ/$) must precede kimi (213 IQ/$)")
        self.assertLess(k, u, "unmapped route must sort last")
