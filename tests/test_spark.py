"""Tests for the ask-history sparkline: series extraction + SVG render."""

from __future__ import annotations

import unittest


class AskSeriesTest(unittest.TestCase):
    """rundata.ask_series(): dated snapshots + live point, gaps skipped."""

    @classmethod
    def setUpClass(cls):
        import importlib.util

        from probe.registry import repo_root

        path = repo_root() / "site" / "rundata.py"
        spec = importlib.util.spec_from_file_location("watch_rundata_spark", path)
        assert spec and spec.loader
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)

    @staticmethod
    def _dated_payload(route, a_in, a_out):
        return {"routes": {route: {"ask_in": a_in, "ask_out": a_out}}}

    def test_series_across_days_plus_current(self):
        route = "ali/qwen3.8-max"
        dated = [
            ("2026-08-27", self._dated_payload(route, 0.014, 0.042)),
            ("2026-08-28", self._dated_payload(route, 0.15, 0.45)),
            ("2026-08-29", self._dated_payload(route, 0.14, 0.42)),
        ]
        current = {"generated_at": "2026-08-31T12:00:00+00:00",
                   "routes": {route: {"ask_in": 0.16, "ask_out": 0.48}}}
        pts = self.mod.ask_series(dated, route, current)
        self.assertEqual(
            pts,
            [("2026-08-27", 0.014, 0.042), ("2026-08-28", 0.15, 0.45),
             ("2026-08-29", 0.14, 0.42), ("2026-08-31", 0.16, 0.48)],
        )

    def test_gap_days_skipped_not_zeroed(self):
        route = "r/a"
        dated = [
            ("2026-08-27", self._dated_payload(route, 0.1, 0.2)),
            ("2026-08-28", {"routes": {route: {"ask_in": None, "ask_out": None}}}),
            ("2026-08-29", self._dated_payload(route, 0.12, 0.24)),
        ]
        pts = self.mod.ask_series(dated, route)
        self.assertEqual([d for d, *_ in pts], ["2026-08-27", "2026-08-29"])

    def test_no_duplicate_current_day(self):
        route = "r/a"
        dated = [("2026-08-31", self._dated_payload(route, 0.1, 0.2))]
        current = {"generated_at": "2026-08-31T12:00:00+00:00",
                   "routes": {route: {"ask_in": 0.1, "ask_out": 0.2}}}
        self.assertEqual(len(self.mod.ask_series(dated, route, current)), 1)

    def test_unknown_route_empty(self):
        self.assertEqual(self.mod.ask_series([], "ghost/route", None), [])


class AskSparkRenderTest(unittest.TestCase):
    """generate._ask_spark(): inline SVG, two lines, point rules."""

    @classmethod
    def setUpClass(cls):
        import importlib.util

        from probe.registry import repo_root

        path = repo_root() / "site" / "generate.py"
        spec = importlib.util.spec_from_file_location("watch_generate_spark", path)
        assert spec and spec.loader
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)

    def test_renders_svg_with_two_lines(self):
        pts = [("2026-08-27", 0.014, 0.042), ("2026-08-28", 0.15, 0.45),
               ("2026-08-29", 0.14, 0.42)]
        out = self.mod._ask_spark(pts)
        self.assertIn("<svg", out)
        self.assertEqual(out.count("<polyline"), 2)  # in + out
        self.assertIn("2026-08-27", out)  # tooltip range

    def test_lines_carry_no_point_marks(self):
        # Owner 2026-09-08: no dots on either line — the two stroke
        # colors are the only distinction.
        pts = [("2026-08-27", 0.014, 0.042), ("2026-08-28", 0.15, 0.45),
               ("2026-08-29", 0.14, 0.42)]
        out = self.mod._ask_spark(pts)
        self.assertNotIn("s-dot", out)
        self.assertNotIn("s-last", out)
        self.assertNotIn("<circle", out)
        self.assertIn("s-line-out", out)  # the amber distinction survives

    def test_two_distinct_stroke_colors(self):
        # Owner 2026-09-08 regression: --mid had been redefined to amber,
        # so BOTH lines rendered one hue. The spark's classes must map to
        # two DIFFERENT color sources in style.css (teal vs amber).
        import re

        from probe.registry import repo_root

        css = (repo_root() / "site" / "style.css").read_text()
        base = re.search(r"\.ask-spark \.s-line \{[^}]*stroke: ([^;]+);", css).group(1)
        out = re.search(r"\.ask-spark \.s-line-out \{[^}]*stroke: ([^;]+);", css).group(1)
        self.assertNotEqual(base.strip(), out.strip())
        self.assertIn("teal", base)
        self.assertIn("warn", out)

    def test_flat_series_still_renders(self):
        pts = [("2026-08-27", 0.1, 0.2), ("2026-08-28", 0.1, 0.2)]
        out = self.mod._ask_spark(pts)
        self.assertIn("<svg", out)

    def test_single_point_renders_nothing(self):
        self.assertEqual(self.mod._ask_spark([("2026-08-27", 0.1, 0.2)]), "")

    def test_empty_renders_nothing(self):
        self.assertEqual(self.mod._ask_spark([]), "")

    def test_spike_shares_one_scale(self):
        # out >> in: both lines must fit the same viewBox height (26)
        pts = [("2026-08-27", 0.014, 0.042), ("2026-08-28", 0.15, 0.45)]
        out = self.mod._ask_spark(pts)
        self.assertIn('height="26"', out)
        for chunk in out.split("cy=")[1:]:
                y = float(chunk.split('"')[1])  # chunk starts with the quoted value
                self.assertGreaterEqual(y, 0.0)
                self.assertLessEqual(y, 26.0)


if __name__ == "__main__":
    unittest.main()
