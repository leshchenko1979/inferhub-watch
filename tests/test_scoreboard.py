"""predictor_scoreboard: the Value leg's RESOLUTION labelling.

HQ 2026-09-12: the hourly legs report `value_regret.n = 0` with
`pass: false`. That is a resolution limit of the INPUT, not a measured
failure, and the label must say WHICH limit:

- the hourly series is rebuilt from `usage_logs` and carries no D3 `iq`
  stamp, so there is no as-of IQ to read at an hourly boundary — a PLUMBING
  gap; and
- IQ's own resolution is a DAY (`data/intelligence.json` is one git-tracked
  version per sweep day), so the input would be day-resolution even once
  stamped — the RESOLUTION limit that no volume of traffic closes.

The two are stated separately because "the leg cannot resolve at all" would
be an overstatement: a date->IQ map IS derivable from the same git history
D3 backfilled from, so resolving it is unbuilt, not impossible. A future
reader must not be able to read "n=0 -> fail" as evidence either way.
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
        self.assertEqual(res["limit"], "no as-of IQ on the hourly series")
        self.assertIn("NOT a measured failure", res["note"])

    def test_unresolved_leg_names_the_plumbing_gap_not_an_impossibility(self) -> None:
        # The honest scope: the hourly series carries no D3 `iq` stamp AND
        # IQ's resolution is a day. "Cannot resolve at all" would overstate
        # it — a date->IQ map is derivable from the same git history D3 used.
        res = psb._value_resolution(0)
        self.assertIn("carries no D3 `iq` stamp", res["note"])
        self.assertIn("resolution is a DAY", res["note"])
        self.assertNotIn("cannot resolve", res["note"])
        self.assertNotIn("AT ALL", res["note"])

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

class CliArgvTest(unittest.TestCase):
    def test_main_takes_an_explicit_argv_and_writes_only_where_told(self) -> None:
        # The hourly sync calls main([]) IN-PROCESS. Were main() to read
        # sys.argv, it would parse the sync's own --days flag and die. Pin
        # both halves: an explicit argv is honoured, and --no-pg skips
        # Postgres so the call is offline and writes only to --out.
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "scoreboard.json"
            rc = psb.main(["--no-pg", "--out", str(out)])
            self.assertEqual(rc, 0)
            artifact = json.loads(out.read_text())
            self.assertIn("daily", artifact)
            self.assertNotIn("hourly_cumulative", artifact)


if __name__ == "__main__":
    unittest.main()
