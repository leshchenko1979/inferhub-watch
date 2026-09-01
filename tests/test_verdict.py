"""Verdict ticket: winner, runner-up, floor fallback, graceful degradation."""

import importlib.util
import sys
import unittest
from unittest import mock

from probe.registry import repo_root

SITE = str(repo_root() / "site")
if SITE not in sys.path:
    sys.path.insert(0, SITE)

QWEN = {"route": "ali/qwen3.8-max", "source": "usage-logs", "ask_in": 0.16,
        "ask_out": 0.48, "eff_per_mtok": 0.07, "reqs": 10, "tok_in": 1000,
        "tok_out": 100, "cost_usdc": 0.1, "cache_pct": 0.5}
LUNA = {"route": "cx/gpt-5.6-luna", "source": "usage-logs", "ask_in": 0.0016,
        "ask_out": 0.0096, "eff_per_mtok": 0.0085, "reqs": 5, "tok_in": 500,
        "tok_out": 50, "cost_usdc": 0.05, "cache_pct": 0.0}
GHOST = {"route": "cx/gpt-5.6-ghost", "source": "catalog", "ask_in": 0.0001,
         "ask_out": 0.0009, "eff_per_mtok": 0.0002, "reqs": 0, "tok_in": 0,
         "tok_out": 0, "cost_usdc": 0.0, "cache_pct": None}
SERIES = [
    ("2026-08-30", 0.020, 0.060),
    ("2026-08-31", 0.016, 0.052),
    ("2026-09-01", 0.012, 0.045),
]
SLUGS = {"ali/qwen3.8-max": "qwen-slug", "cx/gpt-5.6-luna": "luna-slug",
         "cx/gpt-5.6-ghost": "ghost-slug"}


def _intel():
    return {"models": {"qwen-slug": {"iq": 58.1}, "luna-slug": {"iq": 52.3},
                       "ghost-slug": {"iq": 56.0}}}


class VerdictTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = repo_root() / "site" / "generate.py"
        spec = importlib.util.spec_from_file_location("watch_generate_verdict", path)
        assert spec and spec.loader
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)

    def _patched(self, payload_rows):
        import rundata

        payload = {"range": "30d", "requests_scanned": 100,
                   "routes": {r["route"]: r for r in payload_rows}}
        return mock.patch.object(rundata, "pricing_rows", lambda p: payload_rows), \
            mock.patch.object(rundata, "load_intelligence", lambda root: _intel()), \
            mock.patch.object(rundata, "aa_slug", lambda route: SLUGS.get(route)), \
            mock.patch.object(rundata, "load_dated_pricing", lambda root: {}), \
            mock.patch.object(rundata, "ask_series", lambda d, r, p: SERIES)

    def test_winner_alternate_and_ratio(self):
        patches = self._patched([QWEN, LUNA])
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            out = self.mod.verdict_section({"range": "30d"})
        # luna wins: 52.3 / 0.0085 = 6,153 IQ/$ vs qwen 58.1 / 0.07 = 830
        self.assertIn("cx/gpt-5.6-luna", out)
        self.assertIn("6,153", out)
        self.assertIn("Alternate:", out)
        self.assertIn("ali/qwen3.8-max", out)
        self.assertIn("830", out)

    def test_gate_pass_flips_ranking_to_projection(self):
        import probe.official_compare as oc

        gate = {"pass": True, "n": 20, "within": 20, "share": 1.0, "tol": 0.2}
        # production payload always carries routes (load_pricing enforces
        # it) and _proj_eff reads stats from it — mirror that shape here
        payload = {"range": "30d", "requests_scanned": 100,
                   "routes": {QWEN["route"]: QWEN, LUNA["route"]: LUNA}}
        patches = self._patched([QWEN, LUNA])
        with mock.patch.object(oc, "projection_gate", lambda d: gate), \
             mock.patch.object(oc, "projection_hit", lambda *a, **k: (0.5, "ok")), \
             patches[0], patches[1], patches[2], patches[3], patches[4]:
            out = self.mod.verdict_section(payload)
        # luna now ranks on its projected eff (current asks at 50% hit),
        # not the realized 6,153 IQ/$
        proj = oc.inferhub_eff(LUNA, hit=0.5)
        self.assertIn("cx/gpt-5.6-luna", out)
        self.assertIn(f"{52.3 / proj:,.0f}", out)
        self.assertNotIn("6,153", out)
        self.assertIn("projects", out)

    def test_billed_rows_beat_floor_rows(self):
        patches = self._patched([QWEN, LUNA, GHOST])
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            out = self.mod.verdict_section({"range": "30d"})
        # ghost's floor ask yields 280,000 IQ/$ — must NOT win: no billed traffic
        self.assertNotIn("cx/gpt-5.6-ghost", out)
        self.assertIn("cx/gpt-5.6-luna", out)

    def test_ask_trend_and_cache_in_reason(self):
        patches = self._patched([QWEN, LUNA])
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            out = self.mod.verdict_section({"range": "30d"})
        self.assertIn("ask down 2 sweeps straight", out)
        self.assertIn("0% cached", out)

    def test_graceful_without_intelligence(self):
        import rundata

        with mock.patch.object(rundata, "pricing_rows", lambda p: [QWEN]), \
                mock.patch.object(rundata, "load_intelligence", lambda root: {}):
            self.assertEqual(self.mod.verdict_section({"range": "30d"}), "")

    def test_empty_payload_is_silent(self):
        self.assertEqual(self.mod.verdict_section(None), "")


if __name__ == "__main__":
    unittest.main()
