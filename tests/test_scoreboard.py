"""predictor_scoreboard: the Value leg's RESOLUTION labelling.

HQ 2026-09-12: the hourly legs report `value_regret.n = 0` with
`pass: false`. That is a resolution limit of the INPUT — IQ history is
daily-only (data/intelligence.json is one git-tracked version per sweep day,
and the dated snapshots are daily too), so no hourly boundary has an as-of
IQ. A future reader must not be able to read "n=0 -> fail" as evidence.
"""

from __future__ import annotations

import importlib.util
import unittest


def _load():
    spec = importlib.util.spec_from_file_location(
        "psb", "scripts/predictor_scoreboard.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


psb = _load()


class ValueResolutionTest(unittest.TestCase):
    def test_unresolved_leg_is_labelled_not_bare_false(self) -> None:
        res = psb._value_resolution(0)
        self.assertEqual(res["status"], "unresolved")
        self.assertEqual(res["limit"], "IQ history is daily-only")
        self.assertIn("NOT a measured failure", res["note"])

    def test_measured_leg_names_its_basis(self) -> None:
        res = psb._value_resolution(10)
        self.assertEqual(res["status"], "measured")
        self.assertEqual(res["n"], 10)
        self.assertIn("as-of iq stamp", res["basis"])
        self.assertNotIn("note", res)

    def test_score_always_carries_the_resolution_block(self) -> None:
        # Whatever the cadence, the ordering block states WHY the Value leg
        # has the n it has, so no consumer reads a bare `pass: false`.
        blk = psb.score([], {})
        self.assertIn("value_resolution", blk["ordering"])
        self.assertEqual(blk["ordering"]["value_resolution"]["status"],
                         "unresolved")
        self.assertFalse(blk["ordering"]["value_regret"]["pass"])


if __name__ == "__main__":
    unittest.main()
