"""Tests for error_stats() and the #pricing reliability sub-table."""

from __future__ import annotations

import unittest

from probe.pricing import error_stats


def _row(model: str, *, status: str = "ok", http: int = 200) -> dict:
    return {"ts": "2026-09-01T00:00:00Z", "status": status, "http_status": http,
            "model": model, "prompt_tokens": 100, "completion_tokens": 10,
            "cached_tokens": 0, "cost_consumer_usdc": "0.001"}


class ErrorStatsTest(unittest.TestCase):
    """error_stats(): counts, code breakdown, per-model rollup."""

    def test_all_ok(self):
        st = error_stats([_row("m/a"), _row("m/a"), _row("m/b")])
        self.assertEqual(st["total"], 3)
        self.assertEqual(st["failed"], 0)
        self.assertEqual(st["rate_pct"], 0.0)
        self.assertEqual(st["codes"], {})
        self.assertEqual(st["by_model"]["m/a"], {"reqs": 2, "failed": 0, "codes": {}})

    def test_failed_rows_counted_with_codes(self):
        rows = [_row("m/a"), _row("m/a", status="failed", http=502),
                _row("m/a", status="failed", http=502),
                _row("m/b", status="failed", http=429)]
        st = error_stats(rows)
        self.assertEqual(st["total"], 4)
        self.assertEqual(st["failed"], 3)
        self.assertEqual(st["rate_pct"], 75.0)
        self.assertEqual(st["codes"], {"502": 2, "429": 1})
        self.assertEqual(st["by_model"]["m/a"]["failed"], 2)
        self.assertEqual(st["by_model"]["m/a"]["codes"], {"502": 2})

    def test_models_sorted_by_failed_desc(self):
        st = error_stats([_row("m/clean"), _row("m/broken", status="failed", http=502)])
        self.assertEqual(list(st["by_model"]), ["m/broken", "m/clean"])

    def test_null_http_becomes_unknown(self):
        row = _row("m/a", status="failed", http=502)
        row["http_status"] = None
        st = error_stats([row])
        self.assertEqual(st["codes"], {"unknown": 1})

    def test_empty_rows(self):
        st = error_stats([])
        self.assertEqual(st["total"], 0)
        self.assertIsNone(st["rate_pct"])

    def test_rows_without_model_skipped(self):
        st = error_stats([{"ts": "x", "status": "failed", "http_status": 502, "model": ""}])
        self.assertEqual(st["total"], 1)  # window size, mirrors requests_scanned
        self.assertEqual(st["failed"], 0)  # unattributable row is not counted
        self.assertEqual(st["by_model"], {})


class ErrorsTableRenderTest(unittest.TestCase):
    """errors_table() renders the reliability block inside #pricing."""

    @classmethod
    def setUpClass(cls):
        import importlib.util

        from probe.registry import repo_root

        path = repo_root() / "site" / "generate.py"
        spec = importlib.util.spec_from_file_location("watch_generate_errors", path)
        assert spec and spec.loader
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)

    def _payload(self, errors):
        return {"range": "30d", "requests_scanned": 10, "routes": {}, "errors": errors}

    def test_headline_and_rows(self):
        errors = {"total": 4, "failed": 3, "rate_pct": 75.0,
                  "codes": {"502": 2, "429": 1},
                  "by_model": {"m/a": {"reqs": 3, "failed": 2, "codes": {"502": 2}},
                               "m/b": {"reqs": 1, "failed": 1, "codes": {"429": 1}}}}
        out = self.mod.errors_table(self._payload(errors))
        self.assertIn("3&#8202;/&#8202;4 failed", out)
        self.assertIn("75% of requests", out)
        self.assertIn("502&#215;2", out)
        self.assertIn("<code>m/a</code>", out)
        self.assertIn('class="chip bad"', out)

    def test_clean_window_gets_ok_chip(self):
        errors = {"total": 5, "failed": 0, "rate_pct": 0.0, "codes": {},
                  "by_model": {"m/a": {"reqs": 5, "failed": 0, "codes": {}}}}
        out = self.mod.errors_table(self._payload(errors))
        self.assertIn('class="chip ok"', out)
        self.assertIn("0.0%", out)

    def test_no_errors_block_renders_nothing(self):
        self.assertEqual(self.mod.errors_table({"range": "30d", "routes": {}}), "")

    def test_empty_window_renders_nothing(self):
        self.assertEqual(self.mod.errors_table(self._payload({"total": 0, "failed": 0})), "")


if __name__ == "__main__":
    unittest.main()
