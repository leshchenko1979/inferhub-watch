"""Tests for probe/pgstore.py — Postgres usage-log cache (P0a)."""
from __future__ import annotations

import unittest
from unittest import mock

from probe import pgstore


class RowFieldsTests(unittest.TestCase):
    def test_valid_row_is_parsed(self) -> None:
        row = {
            "id": "d1b2c3a4-1111-2222-3333-444455556666",
            "ts": "2026-09-07T08:00:00Z",
            "model": "cb/model",
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "cached_tokens": 8,
            "ttft_ms": "900",
            "duration_ms": "4200",
            "ask_input_per_mtok": "0.01",
            "ask_output_per_mtok": "0.03",
            "cost_consumer_usdc": "0.001",
        }
        fields = pgstore.row_fields(row)
        self.assertIsNotNone(fields)
        self.assertEqual(fields[0], row["id"])
        self.assertEqual(fields[1], row["ts"])
        self.assertEqual(fields[3], 10)
        self.assertEqual(fields[5], 8)
        self.assertEqual(fields[6], "900")

    def test_junk_id_rejected(self) -> None:
        row = {"id": "x", "ts": "2026-09-07T08:00:00Z", "model": "m"}
        self.assertIsNone(pgstore.row_fields(row))

    def test_missing_ts_rejected(self) -> None:
        row = {"id": "test-req-0001", "model": "m"}
        self.assertIsNone(pgstore.row_fields(row))

    def test_missing_model_accepted_with_empty(self) -> None:
        row = {"id": "test-req-0001", "ts": "2026-09-07T08:00:00Z"}
        fields = pgstore.row_fields(row)
        self.assertIsNotNone(fields)
        self.assertEqual(fields[2], "")

    def test_optional_fields_none_when_absent(self) -> None:
        row = {"id": "test-req-0001", "ts": "2026-09-07T08:00:00Z",
               "model": "m"}
        fields = pgstore.row_fields(row)
        self.assertIsNone(fields[6])  # ttft_ms
        self.assertIsNone(fields[10])  # cost

    def test_bad_numeric_becomes_none(self) -> None:
        row = {"id": "test-req-0001", "ts": "2026-09-07T08:00:00Z",
               "model": "m", "cost_consumer_usdc": "not-a-number"}
        fields = pgstore.row_fields(row)
        self.assertIsNone(fields[10])


class UpsertTests(unittest.TestCase):
    """execute_values SQL shape, against a mocked connection."""

    def _conn(self) -> mock.Mock:
        conn = mock.MagicMock()
        conn.cursor.return_value.__enter__ = mock.Mock()
        conn.cursor.return_value.__exit__ = mock.Mock(return_value=False)
        return conn

    def test_upsert_commits_and_counts(self) -> None:
        conn = self._conn()
        rows = [{"id": "test-req-0001", "ts": "2026-09-07T08:00:00Z",
                 "model": "m", "prompt_tokens": 1}]
        with mock.patch.object(pgstore, "_connect", return_value=conn), \
                mock.patch.object(pgstore, "ensure_schema"), \
                mock.patch("psycopg2.extras.execute_values"):
            n = pgstore.upsert_rows(rows, conn=conn)
        self.assertEqual(n, 1)
        conn.commit.assert_called_once()

    def test_upsert_all_malformed_writes_nothing(self) -> None:
        conn = self._conn()
        rows = [{"id": "x", "ts": "2026-09-07T08:00:00Z", "model": "m"}]
        with mock.patch.object(pgstore, "_connect", return_value=conn), \
                mock.patch.object(pgstore, "ensure_schema"), \
                mock.patch("psycopg2.extras.execute_values"):
            n = pgstore.upsert_rows(rows, conn=conn)
        self.assertEqual(n, 0)

    def test_no_env_returns_zero_without_connect(self) -> None:
        with mock.patch.object(pgstore, "load_env",
                               return_value={}):
            self.assertEqual(pgstore.upsert_rows([]), 0)
            self.assertIsNone(pgstore.latest_ts())
            self.assertEqual(pgstore.rows_since("2026-09-01"), [])


class PublishTests(unittest.TestCase):
    """Gate + per-route basis publication, against a mocked connection."""

    def _conn(self) -> mock.Mock:
        conn = mock.MagicMock()
        conn.cursor.return_value.__enter__ = mock.Mock()
        conn.cursor.return_value.__exit__ = mock.Mock(return_value=False)
        return conn

    def test_ddl_declares_both_published_tables(self) -> None:
        self.assertIn("projection_gate", pgstore.GATE_DDL)
        self.assertIn("route_basis", pgstore.ROUTE_BASIS_DDL)

    def test_gate_ddl_retires_the_rank_fidelity_columns(self) -> None:
        # The verdict is the top-1 crown backtest now, so the rank-fidelity
        # columns are superseded and dropped - no stale bar/rho schema survives.
        self.assertNotIn("within integer", pgstore.GATE_DDL)
        self.assertNotIn("bar numeric", pgstore.GATE_DDL)
        self.assertNotIn("rho_median numeric", pgstore.GATE_DDL)
        self.assertIn("land integer", pgstore.GATE_DDL)
        self.assertIn("tol numeric", pgstore.GATE_DDL)

    def test_ensure_schema_executes_every_ddl(self) -> None:
        conn = self._conn()
        pgstore.ensure_schema(conn)
        cur = conn.cursor.return_value.__enter__.return_value
        executed = [c.args[0] for c in cur.execute.call_args_list]
        self.assertEqual(len(executed), 4)
        for ddl in (pgstore.DDL, pgstore.GATE_DDL, pgstore.ROUTE_BASIS_DDL,
                    pgstore.GRANT_DDL):
            self.assertIn(ddl, executed)

    def test_grant_ddl_is_role_guarded(self) -> None:
        # The dashboard reads as inferhub_ro; without the grant every panel
        # fails with "permission denied" on the live surface only.
        self.assertIn("inferhub_ro", pgstore.GRANT_DDL)
        self.assertIn("pg_roles", pgstore.GRANT_DDL)
        self.assertIn("route_basis", pgstore.GRANT_DDL)
        self.assertIn("projection_gate", pgstore.GRANT_DDL)

    def test_gate_upsert_passes_verdict_and_commits(self) -> None:
        conn = self._conn()
        pgstore.publish_projection_gate(conn, {
            "n": 14, "land": 12, "share": 0.857, "tol": 0.15, "min_n": 10,
            "pass": True})
        cur = conn.cursor.return_value.__enter__.return_value
        sql, params = cur.execute.call_args.args
        self.assertIn("insert into projection_gate", sql)
        self.assertIn("on conflict (id) do update", sql)
        self.assertEqual(
            params, ("projection_gate", True, 14, 12, 0.857, 0.15, 10))
        conn.commit.assert_called_once()

    def test_gate_upsert_defaults_missing_pass_to_false(self) -> None:
        conn = self._conn()
        pgstore.publish_projection_gate(conn, {"n": 0})
        cur = conn.cursor.return_value.__enter__.return_value
        self.assertIs(cur.execute.call_args.args[1][1], False)

    def test_route_basis_replaces_the_whole_set(self) -> None:
        conn = self._conn()
        n = pgstore.publish_route_basis(conn, [
            {"route": "r/a", "realized": 0.0005, "projected": None},
            {"route": "r/b", "realized": None, "projected": 0.001},
        ], snapshot_at="2026-09-11T07:26:11+00:00")
        self.assertEqual(n, 2)
        cur = conn.cursor.return_value.__enter__.return_value
        statements = [c.args[0] for c in cur.execute.call_args_list]
        self.assertTrue(any("delete from route_basis" in s for s in statements))
        inserted = cur.executemany.call_args.args
        self.assertIn("insert into route_basis", inserted[0])
        self.assertEqual(inserted[1], [
            ("r/a", 0.0005, None, "2026-09-11T07:26:11+00:00"),
            ("r/b", None, 0.001, "2026-09-11T07:26:11+00:00"),
        ])
        conn.commit.assert_called_once()

    def test_route_basis_empty_still_clears(self) -> None:
        conn = self._conn()
        self.assertEqual(pgstore.publish_route_basis(conn, []), 0)
        cur = conn.cursor.return_value.__enter__.return_value
        self.assertIn("delete from route_basis",
                      cur.execute.call_args.args[0])


class LoadRouteMetricsTests(unittest.TestCase):
    def _conn(self) -> mock.Mock:
        conn = mock.MagicMock()
        conn.cursor.return_value.__enter__ = mock.Mock()
        conn.cursor.return_value.__exit__ = mock.Mock(return_value=False)
        return conn

    def test_load_route_metrics_no_env(self) -> None:
        with mock.patch.object(pgstore, "load_env", return_value={}):
            models, max_updated = pgstore.load_route_metrics()
            self.assertEqual(models, {})
            self.assertIsNone(max_updated)

    def test_load_route_metrics_success(self) -> None:
        from datetime import datetime, timezone
        conn = self._conn()
        cur = conn.cursor.return_value.__enter__.return_value
        dt = datetime(2026, 9, 12, 3, 0, 0, tzinfo=timezone.utc)
        cur.fetchall.return_value = [
            ("cb/model-a", "0.00015", "0.00060", "0.15", "0.60", True, "39.5", dt),
            ("ag/model-b", None, None, "0.50", "1.50", False, None, dt),
        ]
        models, max_updated = pgstore.load_route_metrics(conn=conn)
        self.assertEqual(len(models), 2)
        self.assertEqual(models["cb/model-a"]["ask_in"], 0.00015)
        self.assertEqual(models["cb/model-a"]["ask_out"], 0.0006)
        self.assertTrue(models["cb/model-a"]["supports_cache"])
        self.assertEqual(models["cb/model-a"]["iq"], 39.5)
        self.assertIsNone(models["ag/model-b"]["ask_in"])
        self.assertEqual(max_updated, dt)
        cur.executemany.assert_not_called()

    def test_route_basis_skips_rows_without_a_route(self) -> None:
        conn = self._conn()
        n = pgstore.publish_route_basis(conn, [{"route": "", "realized": 1.0}])
        self.assertEqual(n, 0)

if __name__ == "__main__":
    unittest.main()
