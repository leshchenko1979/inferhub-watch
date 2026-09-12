"""#27 Finding B — the snapshot must declare the window it ACTUALLY covers.

The defect: `range: 30d` sat beside `requests_scanned: 12000`, and 12,000
Management-API rows are ~11.8 h of data. A capped pull read as a 30-day
aggregate. Two laws under test:

1. **The window is declared, not implied** — every snapshot carries
   `window` {first_ts, last_ts, hours, coverage_pct} for the rows it used.
2. **Postgres is not optional when it is configured** — credentials present
   + a failing connection/window RAISES and `main()` exits non-zero, instead
   of silently degrading onto the capped API path.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from probe import pricing  # noqa: E402
from probe.pricing import PgRequiredError, _window_of  # noqa: E402


def _rows(span_hours: float, n: int = 5) -> list[dict]:
    """`n` rows spread across the last `span_hours`, newest last."""
    now = datetime.now(timezone.utc)
    out = []
    for i in range(n):
        ts = now - timedelta(hours=span_hours * (n - 1 - i) / max(n - 1, 1))
        out.append({
            "ts": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "model": "cp/xai/grok-4.6",
            "status": "ok",
            "prompt_tokens": 1000,
            "completion_tokens": 100,
            "cached_tokens": 0,
            "cost_consumer_usdc": "0.001",
            "ask_input_per_mtok": "1",
            "ask_output_per_mtok": "3",
        })
    return out


class WindowOfTests(unittest.TestCase):
    """`_window_of` — the coverage block itself."""

    def test_reports_the_true_span_and_coverage(self):
        w = _window_of(_rows(11.81, n=3), "30d")
        self.assertEqual(w["rows"], 3)
        self.assertEqual(w["declared_range"], "30d")
        self.assertEqual(w["declared_hours"], 720.0)
        self.assertAlmostEqual(w["hours"], 11.81, places=1)
        self.assertAlmostEqual(w["coverage_pct"], 1.64, places=1)
        self.assertTrue(w["first_ts"] < w["last_ts"])

    def test_full_window_reads_as_complete(self):
        w = _window_of(_rows(720.0, n=4), "30d")
        self.assertAlmostEqual(w["hours"], 720.0, places=1)
        self.assertAlmostEqual(w["coverage_pct"], 100.0, places=1)

    def test_hourly_range_scales_the_denominator(self):
        w = _window_of(_rows(24.0, n=3), "24h")
        self.assertEqual(w["declared_hours"], 24.0)
        self.assertAlmostEqual(w["coverage_pct"], 100.0, places=1)

    def test_empty_rows_declare_nothing_rather_than_zero_hours(self):
        w = _window_of([], "30d")
        self.assertEqual(w["rows"], 0)
        self.assertIsNone(w["hours"])
        self.assertIsNone(w["coverage_pct"])
        self.assertIsNone(w["first_ts"])

    def test_unparseable_timestamp_does_not_raise(self):
        w = _window_of([{"ts": "not-a-time"}, {"ts": "2026-08-27T10:00:00Z"}], "30d")
        self.assertEqual(w["rows"], 2)
        self.assertIsNone(w["hours"])

    def test_unknown_range_leaves_coverage_undefined(self):
        w = _window_of(_rows(5.0, n=2), "forever")
        self.assertIsNone(w["declared_hours"])
        self.assertIsNone(w["coverage_pct"])
        self.assertIsNotNone(w["hours"])


class LogRowsSourceTests(unittest.TestCase):
    """`_log_rows` — Postgres when configured, API only when it is not."""

    def test_no_credentials_uses_the_management_api(self):
        api_rows = _rows(11.81, n=4)
        with mock.patch("probe.pgstore.load_env", return_value={}), \
             mock.patch.object(pricing, "fetch_log_rows", return_value=api_rows) as f:
            rows, source = pricing._log_rows("k")
        self.assertEqual(source, "api")
        self.assertEqual(rows, api_rows)
        self.assertEqual(f.call_args.kwargs["max_pages"], pricing.MAX_PAGES)

    def test_credentials_present_uses_postgres_and_stamps_pg(self):
        pg_rows = _rows(720.0, n=4)
        conn = mock.MagicMock()
        with mock.patch("probe.pgstore.load_env", return_value={"PGPASSWORD": "x"}), \
             mock.patch("probe.pgstore._connect", return_value=conn), \
             mock.patch("probe.pgstore.latest_ts", return_value=datetime.now(timezone.utc)), \
             mock.patch("probe.pgstore.rows_since", return_value=pg_rows), \
             mock.patch.object(pricing, "fetch_log_rows") as f:
            rows, source = pricing._log_rows("k")
        self.assertEqual(source, "pg")
        self.assertEqual(rows, pg_rows)
        f.assert_not_called()
        conn.close.assert_called_once()

    def test_connect_failure_with_credentials_raises_and_never_falls_back(self):
        with mock.patch("probe.pgstore.load_env", return_value={"PGPASSWORD": "x"}), \
             mock.patch("probe.pgstore._connect", side_effect=OSError("no route to host")), \
             mock.patch.object(pricing, "fetch_log_rows") as f:
            with self.assertRaises(PgRequiredError) as ctx:
                pricing._log_rows("k")
        self.assertIn("no route to host", str(ctx.exception))
        f.assert_not_called()

    def test_window_query_failure_with_credentials_raises(self):
        conn = mock.MagicMock()
        with mock.patch("probe.pgstore.load_env", return_value={"PGPASSWORD": "x"}), \
             mock.patch("probe.pgstore._connect", return_value=conn), \
             mock.patch("probe.pgstore.latest_ts", side_effect=RuntimeError("permission denied")), \
             mock.patch.object(pricing, "fetch_log_rows") as f:
            with self.assertRaises(PgRequiredError):
                pricing._log_rows("k")
        f.assert_not_called()
        conn.close.assert_called_once()

    def test_credentials_present_but_empty_window_raises(self):
        conn = mock.MagicMock()
        with mock.patch("probe.pgstore.load_env", return_value={"PGPASSWORD": "x"}), \
             mock.patch("probe.pgstore._connect", return_value=conn), \
             mock.patch("probe.pgstore.latest_ts", return_value=None), \
             mock.patch.object(pricing, "fetch_log_rows") as f:
            with self.assertRaises(PgRequiredError):
                pricing._log_rows("k")
        f.assert_not_called()


class MainExitCodeTests(unittest.TestCase):
    """A configured-but-broken Postgres must fail the job, not warn."""

    def test_pg_failure_exits_non_zero(self):
        with mock.patch.dict(os.environ, {"INFERHUB_API_KEY": "k"}), \
             mock.patch.object(pricing, "snapshot_routes", return_value=(["cp/x"], [])), \
             mock.patch.object(pricing, "snapshot", side_effect=PgRequiredError("creds present")), \
             mock.patch.object(pricing, "ATTEMPTS", 1):
            self.assertEqual(pricing.main(), 1)

    def test_ordinary_failure_keeps_the_old_file_and_exits_zero(self):
        with mock.patch.dict(os.environ, {"INFERHUB_API_KEY": "k"}), \
             mock.patch.object(pricing, "snapshot_routes", return_value=(["cp/x"], [])), \
             mock.patch.object(pricing, "snapshot", side_effect=RuntimeError("api 502")), \
             mock.patch.object(pricing, "ATTEMPTS", 1):
            self.assertEqual(pricing.main(), 0)

    def test_missing_api_key_exits_zero(self):
        env = {k: v for k, v in os.environ.items() if k != "INFERHUB_API_KEY"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(pricing.main(), 0)


class LoadEnvOverlayTests(unittest.TestCase):
    """CI supplies PG_* as job env vars; the file is a host convenience."""

    def test_environment_wins_over_the_file(self):
        from probe import pgstore
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "pg.env"
            p.write_text("PGHOST=10.0.0.1\nPGPORT=9999\nPGPASSWORD=from-file\n")
            clean = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
            clean.update({"PGPORT": "15432", "PGPASSWORD": "from-env"})
            with mock.patch.dict(os.environ, clean, clear=True):
                env = pgstore.load_env(p)
        self.assertEqual(env["PGPORT"], "15432")
        self.assertEqual(env["PGPASSWORD"], "from-env")
        self.assertEqual(env["PGHOST"], "10.0.0.1")

    def test_file_alone_still_works(self):
        from probe import pgstore
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "pg.env"
            p.write_text("# comment\nPGUSER=inferhub_ci\n\nPGDATABASE=inferhub_logs\n")
            clean = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
            with mock.patch.dict(os.environ, clean, clear=True):
                env = pgstore.load_env(p)
        self.assertEqual(env, {"PGUSER": "inferhub_ci", "PGDATABASE": "inferhub_logs"})

    def test_missing_file_is_not_an_error(self):
        from probe import pgstore
        clean = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
        with mock.patch.dict(os.environ, clean, clear=True):
            env = pgstore.load_env(Path("/nonexistent/pg.env"))
        self.assertEqual(env, {})


if __name__ == "__main__":
    unittest.main()
