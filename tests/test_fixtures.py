"""P2a: site renders end-to-end against the frozen fixture corpus.

The corpus (tests/fixtures/, built by tests/make_fixtures.py) pins the
data contract — generate.py must render a full board from it WITHOUT
touching the live data/ files. These tests reload the fixtures per test
and assert structural invariants, not exact substrings that break on
every copy edit.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

from probe.registry import repo_root

ROOT = repo_root()
sys.path.insert(0, str(ROOT / "site"))

import rundata  # noqa: E402


def _load_generate():
    path = ROOT / "site" / "generate.py"
    spec = importlib.util.spec_from_file_location("watch_generate_p2a", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fixture(name: str):
    return json.loads((ROOT / "tests" / "fixtures" / name).read_text())


class FixtureCorpusTests(unittest.TestCase):
    """The corpus exists and is structurally complete."""

    def test_corpus_files_present(self) -> None:
        for name in ("pricing.json", "run-latest.json", "intelligence.json"):
            self.assertTrue((ROOT / "tests" / "fixtures" / name).is_file(),
                            name)

    def test_corpus_pinned_generated_at(self) -> None:
        payload = _fixture("pricing.json")
        self.assertEqual(payload["generated_at"],
                         "2026-09-07T09:12:37+00:00")

    def test_corpus_has_board_routes_with_billing(self) -> None:
        payload = _fixture("pricing.json")
        billed = [r for r, e in payload["routes"].items()
                  if e.get("reqs")]
        self.assertGreaterEqual(len(billed), 3)
        self.assertIn("ali/qwen3.8-max", payload["routes"])


class RenderAgainstFixturesTests(unittest.TestCase):
    """generate.py renders a complete board from fixtures alone."""

    def setUp(self) -> None:
        self.gen = _load_generate()
        self.pricing = _fixture("pricing.json")
        self.run = _fixture("run-latest.json")
        self.intel = _fixture("intelligence.json")

    def _render(self) -> str:
        with mock.patch.object(self.gen.rundata, "load_pricing",
                               return_value=self.pricing), \
                mock.patch.object(self.gen.rundata, "load_intelligence",
                                  return_value=self.intel), \
                mock.patch.object(self.gen, "load_runs",
                                  return_value=[self.run]):
            return self.gen.index_html([self.run], self.gen.load_aliases(),
                                       self.gen.load_registry())

    def test_board_renders_all_major_sections(self) -> None:
        page = self._render()
        for section in ("id=\"verdict\"", "id=\"pricing\"", "id=\"results\"",
                        "id=\"earlier\"", "id=\"method\""):
            self.assertIn(section, page)

    def test_board_rows_render_from_fixture_routes(self) -> None:
        page = self._render()
        for route in ("ali/qwen3.8-max", "cbcn/glm-5.3-flash"):
            self.assertIn(route, page)

    def test_money_cells_use_fixed_4_decimals(self) -> None:
        import re
        page = self._render()
        # Rate cells (pair-main, scatter values) carry exactly 4 decimals;
        # delta chips and scale-bound prose are deliberately compact.
        cell_re = re.compile(r'class="pair-main">\$([0-9.]+)')
        cells = list(cell_re.finditer(page))
        self.assertGreater(len(cells), 0, "no rate cells found")
        for m in cells:
            dec = m.group(1).split(".")[1] if "." in m.group(1) else ""
            self.assertEqual(len(dec), 4,
                             f"rate cell not fixed-4: {m.group(0)}")

    def test_scatter_plots_fixture_points(self) -> None:
        page = self._render()
        self.assertIn('class="scatter"', page)
        self.assertIn("data-tip", page)

    def test_header_carries_freshness_chip(self) -> None:
        page = self._render()
        self.assertIn("freshness-chip", page)

    def test_render_is_deterministic(self) -> None:
        self.assertEqual(self._render(), self._render())


if __name__ == "__main__":
    unittest.main()
