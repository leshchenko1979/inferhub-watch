"""Task-3 render tests: family anchors in nav, drill-down links,
past-runs hidden note, mobile-fold CSS block (IA overhaul)."""
import importlib.util
import pathlib
import unittest

from probe.registry import repo_root

import sys
sys.path.insert(0, str(repo_root() / "site"))

ROOT = repo_root()


def _load_generate():
    path = repo_root() / "site" / "generate.py"
    spec = importlib.util.spec_from_file_location("watch_generate_ia", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


import tests.test_wiring as tw


class NavFamilyAnchorsTest(tw.ProbeResultsSectionTests):
    def test_nav_lists_family_anchor_for_each_band(self) -> None:
        gen = _load_generate()
        scoring = gen.rundata.scoring_ids(gen.load_registry())
        run = self._run(scoring, [("cp/cline-pass/qwen3.8-max", 2, 93)])
        _, nav = self._page(gen, run)
        self.assertIn('class="nav-fam" href="#fam-qwen3.8-max"', nav)

    def test_family_head_row_carries_matching_anchor_id(self) -> None:
        gen = _load_generate()
        scoring = gen.rundata.scoring_ids(gen.load_registry())
        run = self._run(scoring, [("cp/cline-pass/qwen3.8-max", 2, 93)])
        page, nav = self._page(gen, run)
        self.assertIn('id="fam-qwen3.8-max"', page)          # the band anchor
        self.assertIn('href="#fam-qwen3.8-max"', nav)        # nav jumps to it


class DrillDownLinksTest(tw.ProbeResultsSectionTests):
    def test_route_rows_link_to_check_pages(self) -> None:
        gen = _load_generate()
        scoring = gen.rundata.scoring_ids(gen.load_registry())
        run = self._run(scoring, [("cp/cline-pass/qwen3.8-max", 2, 93)])
        page, _ = self._page(gen, run)
        seg = page[page.find('id="results"'):page.find('id="method"')]
        self.assertIn('class="route-drill"', seg)
        self.assertIn('href="/inferhub-watch/checks/core.html"', seg)
        self.assertIn('href="/inferhub-watch/checks/cache.html"', seg)


class MobileFoldCssTest(unittest.TestCase):
    def test_css_hides_ask_and_window_on_narrow_screens(self) -> None:
        css = (ROOT / "site" / "style.css").read_text()
        self.assertIn('td[data-label="ask in / out"]', css)
        self.assertIn('td[data-label="window"]', css)
        self.assertIn(".nav-fam", css)
        self.assertIn("route-drill", css)
