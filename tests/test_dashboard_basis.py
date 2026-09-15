"""Issue #15 & #23: Best route panels consume canonical view and display Value.

Panels 52..55 display the Best Value route and Alternate route along with their
North Star Value metrics from the canonical `v_model_catalog_metrics` database view.
"""
from __future__ import annotations

import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
DASHBOARD = ROOT / "dashboards" / "watch.json"

PANELS = {
    52: ("Best value route", False),
    53: ("Best Value", True),
    54: ("Alternate route", False),
    55: ("Alt Value", True),
}

def _panels() -> dict:
    data = json.loads(DASHBOARD.read_text())
    return {p["id"]: p for p in data["panels"]}

def _sql(panel: dict) -> str:
    return panel["targets"][0]["rawSql"]

class PanelSqlShapeTests(unittest.TestCase):
    def test_the_four_basis_panels_are_present(self) -> None:
        panels = _panels()
        for pid, (title, _is_val) in PANELS.items():
            self.assertIn(pid, panels, f"panel {pid} missing")
            self.assertEqual(panels[pid]["title"], title)

    def test_every_panel_reads_the_canonical_view(self) -> None:
        for pid in PANELS:
            sql = _sql(_panels()[pid])
            self.assertIn("v_model_catalog_metrics", sql, f"panel {pid} ignores canonical view")

    def test_ranking_orders_by_value_score_desc(self) -> None:
        for pid in PANELS:
            sql = _sql(_panels()[pid])
            self.assertIn(
                "ORDER BY value_score DESC NULLS LAST",
                sql, f"panel {pid} sort law drifted from value_score",
            )

    def test_only_the_alternate_panels_skip_the_first_route(self) -> None:
        panels = _panels()
        for pid in PANELS:
            sql = _sql(panels[pid])
            if pid in (54, 55):
                self.assertIn("OFFSET 1 LIMIT 1", sql, f"panel {pid} not the runner-up")
            else:
                self.assertNotIn("OFFSET", sql, f"panel {pid} should be the leader")

    def test_value_stat_panels_use_short_units(self) -> None:
        panels = _panels()
        for pid, (_title, is_val) in PANELS.items():
            panel = panels[pid]
            if is_val:
                unit = panel.get("fieldConfig", {}).get("defaults", {}).get("unit")
                self.assertEqual(unit, "short", f"panel {pid} missing short unit formatting")


class CatalogViewDryTests(unittest.TestCase):
    def test_catalog_panels_read_the_canonical_view(self) -> None:
        panels = _panels()
        for pid in (5, 20, 21, 52, 53, 54, 55):
            self.assertIn(pid, panels, f"panel {pid} missing")
            sql = _sql(panels[pid])
            self.assertIn("v_model_catalog_metrics", sql, f"panel {pid} does not read canonical view")
            self.assertNotIn("WITH agg AS", sql, f"panel {pid} carries duplicate raw CTEs")


class GateVerdictPanelTests(unittest.TestCase):
    """Panel 72 discloses BOTH legs, and names each by its own column."""

    GATE_PANEL = 72

    def _gate_panel(self) -> dict:
        panels = _panels()
        self.assertIn(self.GATE_PANEL, panels, "panel 72 missing")
        return panels[self.GATE_PANEL]

    def test_the_gate_verdict_column_reads_the_gate_verdict(self) -> None:
        sql = _sql(self._gate_panel())
        self.assertIn(
            "CASE WHEN pass THEN 'PASS' ELSE 'FAIL' END AS \"Gate verdict\"", sql
        )

    def test_the_cost_verdict_column_reads_cost_pass_not_pass(self) -> None:
        sql = _sql(self._gate_panel())
        self.assertIn(
            "CASE WHEN cost_pass THEN 'PASS' ELSE 'FAIL' END AS \"Cost verdict\"",
            sql,
        )

    def test_the_value_verdict_column_reads_value_pass(self) -> None:
        sql = _sql(self._gate_panel())
        self.assertIn("value_pass", sql)

    def test_every_column_the_panel_names_exists_on_the_gate_row(self) -> None:
        from probe import pgstore

        ddl = pgstore.GATE_DDL.lower()
        for column in ("pass", "cost_pass", "n", "land", "share",
                       "value_pass", "value_n", "value_land", "value_share",
                       "value_status", "computed_at"):
            self.assertIn(column, ddl, f"projection_gate.{column} not published")


class LeaderboardPanelTests(unittest.TestCase):
    """Panel 5 ranks catalog routes by proven tier and Value."""

    def test_leaderboard_panel_orders_by_tier_and_value(self) -> None:
        panel = _panels()[5]
        sql = _sql(panel)
        self.assertIn('ORDER BY "Tier" DESC, "Value" DESC NULLS LAST, "IQ per $" DESC NULLS LAST', sql)
        self.assertIn('"Value"', sql)
        self.assertIn('"IQ per $"', sql)
        self.assertIn('"Tier"', sql)


if __name__ == "__main__":
    unittest.main()
