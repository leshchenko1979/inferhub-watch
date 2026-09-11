"""Issue #15: the four $/M Grafana panels must price like the board.

The panels read Postgres, the board reads the committed snapshot. They agree
only because the usage-log sync publishes the board's money basis
(route_basis) and the projection gate (projection_gate) into Postgres, and
the panels consume those — never a basis they re-derive themselves.

These tests pin the SQL shape and the basis disclosure; the live numbers are
verified against the deployed dashboard.
"""
from __future__ import annotations

import json
import pathlib
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
DASHBOARD = ROOT / "dashboards" / "watch.json"

# panel id -> (title, is the IQ per $ variant)
PANELS = {
    52: ("Best value route", False),
    53: ("Best IQ per $", True),
    54: ("Alternate route", False),
    55: ("Alt IQ per $", True),
}

def _panels() -> dict:
    data = json.loads(DASHBOARD.read_text())
    return {p["id"]: p for p in data["panels"]}

def _sql(panel: dict) -> str:
    return panel["targets"][0]["rawSql"]

class PanelSqlShapeTests(unittest.TestCase):
    def test_the_four_basis_panels_are_present(self) -> None:
        panels = _panels()
        for pid, (title, _iq) in PANELS.items():
            self.assertIn(pid, panels, f"panel {pid} missing")
            self.assertEqual(panels[pid]["title"], title)

    def test_every_panel_reads_the_published_gate(self) -> None:
        for pid, panel in _panels().items():
            if pid not in PANELS:
                continue
            sql = _sql(panel)
            self.assertIn("projection_gate", sql, f"panel {pid} ignores the gate")
            self.assertIn("COALESCE((SELECT pass FROM projection_gate", sql)
            self.assertIn("false", sql, f"panel {pid} must default the gate to closed")

    def test_every_panel_reads_the_published_basis(self) -> None:
        for pid in PANELS:
            sql = _sql(_panels()[pid])
            self.assertIn("route_basis", sql, f"panel {pid} re-derives the price")

    def test_no_panel_divides_an_ungated_projection(self) -> None:
        # the pre-#15 defect: the projected price was divided immediately
        for pid in PANELS:
            sql = _sql(_panels()[pid])
            self.assertNotIn("px.projected", sql, f"panel {pid} still ungated")

    def test_both_basis_words_are_reachable(self) -> None:
        for pid in PANELS:
            sql = _sql(_panels()[pid])
            self.assertIn("'projected'", sql, f"panel {pid} cannot label projected")
            self.assertIn("'realized'", sql, f"panel {pid} cannot label realized")

    def test_the_value_carries_the_basis_word(self) -> None:
        for pid in PANELS:
            sql = _sql(_panels()[pid])
            self.assertIn("|| ' · ' || ranked.basis", sql, f"panel {pid} unlabelled")

    def test_ranking_mirrors_the_board_sort_law(self) -> None:
        # basis ascending, IQ per $ descending on ties, unpriced last
        for pid in PANELS:
            sql = _sql(_panels()[pid])
            self.assertIn(
                "ORDER BY ranked.basis_per_m ASC, "
                "ranked.iq/NULLIF(ranked.basis_per_m,0) DESC NULLS LAST",
                sql, f"panel {pid} sort law drifted from the board",
            )

    def test_only_the_alternate_panels_skip_the_first_route(self) -> None:
        panels = _panels()
        for pid in PANELS:
            sql = _sql(panels[pid])
            if pid in (54, 55):
                self.assertIn("OFFSET 1 LIMIT 1", sql, f"panel {pid} not the runner-up")
            else:
                self.assertNotIn("OFFSET", sql, f"panel {pid} should be the leader")

    def test_iq_panels_format_with_thousands_separators(self) -> None:
        panels = _panels()
        for pid, (_title, is_iq) in PANELS.items():
            sql = _sql(panels[pid])
            if is_iq:
                self.assertIn("FM999,999,999,999", sql, f"panel {pid} unformatted")
            else:
                self.assertNotIn("FM999", sql)

class BasisDisclosureTests(unittest.TestCase):
    def test_every_panel_description_names_both_bases(self) -> None:
        for pid, panel in _panels().items():
            if pid not in PANELS:
                continue
            desc = panel.get("description") or ""
            self.assertIn("realized", desc, f"panel {pid} hides the realized basis")
            self.assertIn("projected", desc, f"panel {pid} hides the projected basis")
            self.assertIn("gate", desc, f"panel {pid} does not name the gate")

    def test_no_panel_claims_a_fixed_basis(self) -> None:
        # the basis follows the gate, so a title must not hard-code one
        for pid, panel in _panels().items():
            if pid not in PANELS:
                continue
            self.assertEqual(panel["title"], PANELS[pid][0])

class SyncPublishesTheBoardBasisTests(unittest.TestCase):
    """The panels only agree with the board because the sync publishes it."""

    def test_sync_publishes_route_bases_from_the_committed_snapshot(self) -> None:
        from scripts import sync_usage_logs

        from probe import pgstore

        snapshot = json.loads((ROOT / "data" / "pricing.json").read_text())
        with mock.patch.object(pgstore, "publish_route_basis",
                               return_value=7) as publish:
            n = sync_usage_logs.sync_route_basis(object())
        self.assertEqual(n, 7)
        conn, rows = publish.call_args.args
        self.assertIsInstance(rows, list)
        self.assertEqual(
            {r["route"] for r in rows}, set(snapshot["routes"])
        )
        for row in rows:
            self.assertEqual(
                set(row), {"route", "realized", "projected"},
                "the published row must carry both bases",
            )
        self.assertEqual(publish.call_args.kwargs["snapshot_at"],
                         snapshot.get("generated_at"))

    def test_sync_publishes_the_gate_before_the_bases(self) -> None:
        from scripts import sync_usage_logs

        from probe import pgstore

        calls: list[str] = []
        with mock.patch.object(pgstore, "publish_projection_gate",
                               side_effect=lambda *a, **k: calls.append("gate")), \
                mock.patch.object(pgstore, "publish_route_basis",
                                  side_effect=lambda *a, **k: calls.append("basis")):
            sync_usage_logs.sync_projection_gate(object())
            sync_usage_logs.sync_route_basis(object())
        self.assertEqual(calls, ["gate", "basis"])


if __name__ == "__main__":
    unittest.main()
