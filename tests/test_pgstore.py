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


if __name__ == "__main__":
    unittest.main()
