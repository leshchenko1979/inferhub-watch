"""Unit tests for sync_usage_logs.resolve_slug — the route -> intelligence
slug mapping (issue #6: route_metrics.iq was NULL for every row because the
naive fallback missed most routes)."""

from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path
from typing import ClassVar

import tomllib

from probe.registry import repo_root

_SPEC = importlib.util.spec_from_file_location(
    "sync_usage_logs",
    Path(repo_root()) / "scripts" / "sync_usage_logs.py")
assert _SPEC and _SPEC.loader
sync = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sync)

AA = {"ali/deepseek-v4-flash-0731": "deepseek-v4-flash"}  # explicit override


class NormSlugTests(unittest.TestCase):
    def test_normalizes_case_underscores_and_dots(self) -> None:
        self.assertEqual(sync._norm_slug("Glm_5.3"), "glm-5-3")

    def test_strips_whitespace(self) -> None:
        self.assertEqual(sync._norm_slug("  kimi-k3 "), "kimi-k3")


class ResolveSlugTests(unittest.TestCase):
    SLUGS: ClassVar[dict] = {
        "deepseek-v4-flash": {"iq": 34.5},
        "deepseek-v4-pro": {"iq": 36.3},
        "muse-spark-1-2": {"iq": 39.8},
        "gemini-3-6-flash": {"iq": 34.3},
        "gemini-3-6-flash-low": {"iq": 30.0},
        "qwen3-6-max": {"iq": 28.4},
        "kimi-k2-7-code": {"iq": 26.3},
        "mimo-v2-5-0424": {"iq": 22.3},
        "mimo-v2-5-pro": {"iq": 26.4},
        "claude-4-5-haiku": {"iq": 15.4},
        "claude-4-5-sonnet": {"iq": 19.3},
        "claude-opus-4-7": {"iq": 40.7},
        "claude-opus-4-6": {"iq": 38.0},
        "claude-opus-5": {"iq": 50.7},
        "no-iq-model": {"iq": None},
    }

    def r(self, route: str, aa: dict | None = None) -> str | None:
        return sync.resolve_slug(route, aa or {}, self.SLUGS)

    def test_exact_normalized_tail(self) -> None:
        self.assertEqual(
            sync.resolve_slug("cb/deepseek-v4-pro", {}, self.SLUGS),
            "deepseek-v4-pro")

    def test_aa_override_wins(self) -> None:
        self.assertEqual(
            sync.resolve_slug("ali/deepseek-v4-flash-0731", AA, self.SLUGS),
            "deepseek-v4-flash")

    def test_trailing_date_variant(self) -> None:
        self.assertEqual(self.r("ali/deepseek-v4-flash-0731"),
                         "deepseek-v4-flash")

    def test_suffix_qualifier_dropped(self) -> None:
        self.assertEqual(self.r("ocg/muse-spark-1.2-contributor"),
                         "muse-spark-1-2")

    def test_prefix_match_prefers_iq_bearing_shortest(self) -> None:
        self.assertEqual(self.r("ag/gemini-3.6-flash-high"),
                         "gemini-3-6-flash")
        self.assertEqual(self.r("cmc/Qwen/Qwen3.6-Max-Preview"),
                         "qwen3-6-max")

    def test_word_order_swap(self) -> None:
        # some catalogs publish <vendor>/{model}-{ver}-{tier}; the
        # intelligence slug uses the tier-first order
        self.assertEqual(self.r("cc/haiku-4-5-claude"), "claude-4-5-haiku")
        self.assertEqual(self.r("cc/sonnet-4-5-claude"),
                         "claude-4-5-sonnet")

    def test_base_name_extends_to_known_variant(self) -> None:
        # the route names the base model; the only slugs are its variants
        self.assertEqual(self.r("cbcn/kimi-k2.7"), "kimi-k2-7-code")
        self.assertEqual(self.r("ocg/mimo-v2.5"), "mimo-v2-5-pro")

    def test_qualifier_never_crosses_a_version_boundary(self) -> None:
        # regression: 'claude-opus-4-7-1m' (1m = context tag) must strip to
        # claude-opus-4-7; the fuzzy fallback rated claude-opus-5 at 0.77 and
        # silently reported opus-5's iq for an opus-4.7 route
        self.assertEqual(self.r("cb/claude-opus-4.7-1m"), "claude-opus-4-7")
        self.assertEqual(self.r("ag/claude-opus-4-6-thinking"),
                         "claude-opus-4-6")

    def test_prefix_match_skips_iqless_candidates(self) -> None:
        # only candidate would be iq-less under a strict base match; the
        # resolver still returns the slug, iq stays None upstream
        self.assertEqual(self.r("x/no-iq-model-extra"), "no-iq-model")

    def test_unknown_model_returns_none(self) -> None:
        self.assertIsNone(self.r("ag/gemini-pro-agent"))


class LiveCatalogTests(unittest.TestCase):
    """The done criterion of issue #6: >= 90% of catalog routes resolve to
    an intelligence slug carrying an iq score."""

    def test_at_least_90_percent_of_routes_have_iq(self) -> None:
        root = Path(repo_root())
        catalog = json.loads((root / "data" / "catalog.json").read_text())
        intel = json.loads((root / "data" / "intelligence.json").read_text())
        toml = tomllib.loads((root / "models.toml").read_text()) \
            if (root / "models.toml").exists() else {}
        aa = toml.get("aa") or {}
        slugs = intel.get("models") or {}
        models = catalog.get("models") or {}
        self.assertTrue(models, "catalog.json has no routes")
        hits = sum(
            1 for route in models
            if (slug := sync.resolve_slug(route, aa, slugs))
            and (slugs.get(slug) or {}).get("iq") is not None)
        share = hits / len(models)
        self.assertGreaterEqual(
            share, 0.90, f"only {hits}/{len(models)} routes have iq")

    def test_sync_route_metrics_with_live_models(self) -> None:
        """Verify sync_route_metrics accepts live_models in-memory without error."""
        class DummyCursor:
            def __init__(self):
                self.executed = []

            def execute(self, sql, params=None):
                self.executed.append((sql, params))

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        class DummyConn:
            def cursor(self):
                return DummyCursor()

        conn = DummyConn()
        mock_live_models = {
            "mock/model-1": {
                "ask_in": 0.001,
                "ask_out": 0.002,
                "official_in": 0.01,
                "official_out": 0.02,
                "supports_cache": True,
            }
        }
        res = sync.sync_route_metrics(conn, live_models=mock_live_models)
        self.assertEqual(res, 1)


if __name__ == "__main__":
    unittest.main()
