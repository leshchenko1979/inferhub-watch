"""Retry-once regression tests: a timeout or 5xx in a congested window
(the 2026-09-01 inferhub degradation) must not paint a healthy route bad.
One replay after a 20s wait; recovery is recorded as evidence, never silent.
No real probe spend — every test drives a stub module with a scripted run()."""

from __future__ import annotations

import types
from unittest import mock

import unittest


def _load_run():
    import probe.run as run_mod

    return run_mod


def _cell(status: str, http_status: int | None = None) -> dict:
    cell = {
        "check_id": "core",
        "alias": "a",
        "status": status,
        "summary": (
            f"HTTP {http_status}: upstream unavailable"
            if http_status is not None
            else f"status {status}"
        ),
    }
    if http_status is not None:
        cell["http_status"] = http_status
    return cell


class RetryOnceTests(unittest.TestCase):
    def test_timeout_then_pass_recovers_with_evidence(self) -> None:
        run_mod = _load_run()
        calls = {"n": 0}

        def scripted(client, alias):
            calls["n"] += 1
            if calls["n"] == 1:
                raise TimeoutError("read operation timed out")
            return _cell("pass")

        stub = types.SimpleNamespace(run=scripted)
        with mock.patch.object(run_mod, "load_check_module", return_value=stub):
            with mock.patch.object(run_mod.time, "sleep") as snooze:
                cells, errors = run_mod.collect_cells(
                    object(), ["a"], [{"id": "core"}]
                )
        self.assertEqual(errors, [])
        self.assertEqual(calls["n"], 2)
        snooze.assert_called_once_with(run_mod.RETRY_WAIT_SECONDS)
        self.assertEqual(cells[0]["status"], "pass")
        self.assertIn("read operation timed out", cells[0]["flaky_recovered"])

    def test_5xx_then_pass_recovers(self) -> None:
        run_mod = _load_run()
        calls = {"n": 0}

        def scripted(client, alias):
            calls["n"] += 1
            if calls["n"] == 1:
                return _cell("error", http_status=503)
            return _cell("pass")

        stub = types.SimpleNamespace(run=scripted)
        with mock.patch.object(run_mod, "load_check_module", return_value=stub):
            with mock.patch.object(run_mod.time, "sleep"):
                cells, _ = run_mod.collect_cells(object(), ["a"], [{"id": "core"}])
        self.assertEqual(calls["n"], 2)
        self.assertEqual(cells[0]["status"], "pass")
        self.assertIn("503", cells[0]["flaky_recovered"])

    def test_timeout_twice_stays_error_with_first_attempt_context(self) -> None:
        run_mod = _load_run()

        def scripted(client, alias):
            raise TimeoutError("read operation timed out")

        stub = types.SimpleNamespace(run=scripted)
        with mock.patch.object(run_mod, "load_check_module", return_value=stub):
            with mock.patch.object(run_mod.time, "sleep") as snooze:
                cells, errors = run_mod.collect_cells(
                    object(), ["a"], [{"id": "core"}, {"id": "cache"}]
                )
        self.assertEqual(len(errors), 1)
        snooze.assert_called_once()
        self.assertEqual(cells[0]["status"], "error")
        self.assertEqual(
            cells[0]["first_attempt"], "read operation timed out"
        )
        # Fail-fast still applies after a retried-and-still-down cell.
        self.assertEqual(cells[1]["status"], "skipped")

    def test_assertion_fail_is_never_retried(self) -> None:
        run_mod = _load_run()
        calls = {"n": 0}

        def scripted(client, alias):
            calls["n"] += 1
            return _cell("fail")

        stub = types.SimpleNamespace(run=scripted)
        with mock.patch.object(run_mod, "load_check_module", return_value=stub):
            with mock.patch.object(run_mod.time, "sleep") as snooze:
                cells, _ = run_mod.collect_cells(object(), ["a"], [{"id": "core"}])
        self.assertEqual(calls["n"], 1)
        snooze.assert_not_called()
        self.assertEqual(cells[0]["status"], "fail")
        self.assertNotIn("flaky_recovered", cells[0])

    def test_4xx_error_is_never_retried(self) -> None:
        run_mod = _load_run()
        calls = {"n": 0}

        def scripted(client, alias):
            calls["n"] += 1
            return _cell("error", http_status=400)

        stub = types.SimpleNamespace(run=scripted)
        with mock.patch.object(run_mod, "load_check_module", return_value=stub):
            with mock.patch.object(run_mod.time, "sleep") as snooze:
                cells, _ = run_mod.collect_cells(object(), ["a"], [{"id": "core"}])
        self.assertEqual(calls["n"], 1)
        snooze.assert_not_called()
        self.assertEqual(cells[0]["status"], "error")

    def test_balance_exception_still_aborts_without_retry(self) -> None:
        run_mod = _load_run()

        def scripted(client, alias):
            raise RuntimeError("insufficient_balance for key")

        stub = types.SimpleNamespace(run=scripted)
        with mock.patch.object(run_mod, "load_check_module", return_value=stub):
            with mock.patch.object(run_mod.time, "sleep") as snooze:
                with self.assertRaises(run_mod.BalanceTooLow):
                    run_mod.collect_cells(object(), ["a"], [{"id": "core"}])
        snooze.assert_not_called()

    def test_balance_cell_summary_still_aborts_after_retry(self) -> None:
        run_mod = _load_run()

        # Balance arrives as a 402 error cell with a balance summary.
        def scripted2(client, alias):
            cell = _cell("error", http_status=402)
            cell["summary"] = 'HTTP 402: {"error":{"message":"balance too low"}}'
            return cell

        stub = types.SimpleNamespace(run=scripted2)
        with mock.patch.object(run_mod, "load_check_module", return_value=stub):
            with mock.patch.object(run_mod.time, "sleep"):
                with self.assertRaises(run_mod.BalanceTooLow):
                    run_mod.collect_cells(object(), ["a"], [{"id": "core"}])


if __name__ == "__main__":
    unittest.main()
