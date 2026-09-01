"""Tests for the Artificial Analysis intelligence lane.

Covers the snapshot fetch parser, the route->slug map, and the board's
IQ / IQ-per-$ cells (ontology terms: Intelligence (IQ), IQ per $).
"""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from probe import intelligence  # noqa: E402


def _load_generate():
    spec = importlib.util.spec_from_file_location(
        "site.generate._t", REPO / "site" / "generate.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _entry(iq=57.5, coding=71.5, aa_in=0.15, aa_out=0.5):
    return {
        "slug": "glm-5-3-flash",
        "name": "GLM-5.3-Flash",
        "model_creator": {"name": "Z AI", "slug": "zai"},
        "evaluations": {
            "artificial_analysis_intelligence_index": iq,
            "artificial_analysis_coding_index": coding,
        },
        "pricing": {
            "price_1m_input_tokens": aa_in,
            "price_1m_output_tokens": aa_out,
        },
    }


class FetchModelsTest(unittest.TestCase):
    def test_parses_slug_evals_pricing(self):
        body = {"data": [_entry()]}
        models = intelligence._models_from_body(body)
        self.assertEqual(models["glm-5-3-flash"]["iq"], 57.5)
        self.assertEqual(models["glm-5-3-flash"]["coding"], 71.5)
        self.assertEqual(models["glm-5-3-flash"]["aa_in"], 0.15)
        self.assertEqual(models["glm-5-3-flash"]["aa_out"], 0.5)

    def test_skips_entries_without_slug(self):
        body = {"data": [{"name": "no slug here", "evaluations": {}}]}
        self.assertEqual(intelligence._models_from_body(body), {})

    def test_tolerates_null_evals(self):
        entry = _entry()
        entry["evaluations"] = {"artificial_analysis_intelligence_index": None}
        models = intelligence._models_from_body({"data": [entry]})
        self.assertIsNone(models["glm-5-3-flash"]["iq"])


class SlugMapTest(unittest.TestCase):
    def test_every_board_route_has_a_slug(self):
        from probe.registry import load_aliases

        import tomllib

        data = tomllib.loads((REPO / "models.toml").read_text())
        aa = data.get("aa") or {}
        for alias in load_aliases():
            self.assertIn(alias, aa, f"models.toml [aa] misses {alias}")

    def test_known_slug_values(self):
        import tomllib

        data = tomllib.loads((REPO / "models.toml").read_text())
        self.assertEqual(data["aa"]["zai/glm-5.3-flash"], "glm-5-3-flash")
        self.assertEqual(data["aa"]["cx/gpt-5.6-sol"], "gpt-5-6-sol")
        self.assertEqual(data["aa"]["ali/qwen3.8-max"], "qwen3-8-max")


class IqCellsTest(unittest.TestCase):
    def setUp(self):
        self.gen = _load_generate()
        self.intel = {"models": {"glm-5-3-flash": {"iq": 57.5}}}

    def test_iq_and_per_dollar_render(self):
        cells = self.gen._iq_cells("zai/glm-5.3-flash", 0.021, self.intel)
        self.assertIn(">57.5</td>", cells)
        self.assertIn(">2,738</td>", cells)

    def test_unmapped_route_renders_dashes(self):
        cells = self.gen._iq_cells("ghost/route", 0.5, self.intel)
        self.assertEqual(cells.count("&#8212;"), 2)

    def test_null_iq_renders_dashes(self):
        intel = {"models": {"glm-5-3-flash": {"iq": None}}}
        cells = self.gen._iq_cells("zai/glm-5.3-flash", 0.021, intel)
        self.assertEqual(cells.count("&#8212;"), 2)

    def test_no_snapshot_renders_empty(self):
        cells = self.gen._iq_cells("zai/glm-5.3-flash", 0.021, None)
        self.assertEqual(cells.count("<td"), 2)

    def test_zero_eff_renders_dash_not_crash(self):
        cells = self.gen._iq_cells("zai/glm-5.3-flash", None, self.intel)
        self.assertIn(">57.5</td>", cells)
        self.assertIn("&#8212;</td>", cells)


class SnapshotFormatTest(unittest.TestCase):
    def test_write_snapshot_shape(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            out = intelligence.write_snapshot(
                {"glm-5-3-flash": {"iq": 57.5}}, root
            )
            payload = json.loads(out.read_text())
        self.assertEqual(payload["source"], "artificialanalysis.ai")
        self.assertIn("generated_at", payload)
        self.assertEqual(payload["models"]["glm-5-3-flash"]["iq"], 57.5)


if __name__ == "__main__":
    unittest.main()
