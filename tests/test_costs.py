from __future__ import annotations

import unittest

from probe.costs import attribute_costs, parse_ts, run_window


def _row(
    model: str,
    minute: int,
    second: int = 0,
    prompt: int = 100,
    completion: int = 50,
    cost: str = "0.001000",
    status: str = "ok",
) -> dict:
    return {
        "ts": f"2026-08-27T19:{minute:02d}:{second:02d}.000Z",
        "model": model,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "cached_tokens": 0,
        "cost_consumer_usdc": cost,
        "status": status,
    }


def _cell(alias: str, check: str, usage: dict | None = None) -> dict:
    evidence = {"usage": usage} if usage else {}
    return {
        "check_id": check,
        "alias": alias,
        "status": "pass",
        "evidence": evidence,
    }


def _run(cells: list[dict], *, finished: bool = True, aliases=None) -> dict:
    run = {
        "started_at": "2026-08-27T19:50:00Z",
        "aliases": aliases or ["zai/glm-5.3"],
        "checks": ["core", "cache"],
        "cells": cells,
    }
    if finished:
        run["finished_at"] = "2026-08-27T19:56:00Z"
    return run


class WindowTests(unittest.TestCase):
    def test_window_with_finish(self) -> None:
        start, end = run_window(_run([]))
        self.assertEqual(parse_ts("2026-08-27T19:49:00Z"), start)
        self.assertEqual(end, parse_ts("2026-08-27T19:57:00Z"))  # +60s margin

    def test_legacy_run_scans_back(self) -> None:
        start, _ = run_window(_run([], finished=False))
        self.assertEqual(parse_ts("2026-08-27T19:15:00Z"), start)


class AttributionTests(unittest.TestCase):
    def test_exact_token_match_beats_fleet_noise(self) -> None:
        cell = _cell("zai/glm-5.3", "cache", {"prompt_tokens": 70, "completion_tokens": 18})
        rows = [
            _row("zai/glm-5.3", 51, prompt=999, completion=888, cost="0.500000"),
            _row("zai/glm-5.3", 53, prompt=70, completion=18, cost="0.000013"),
        ]
        summary = attribute_costs(_run([cell]), rows)
        self.assertEqual(cell["cost_usdc"], "0.000013")
        self.assertEqual(cell["cost_match"], "tokens")
        self.assertEqual(summary["total_usdc"], "0.000013")

    def test_single_request_cell_matches_one_row(self) -> None:
        cell = _cell("zai/glm-5.3", "cache", {"prompt_tokens": 1864, "completion_tokens": 6})
        rows = [
            _row("zai/glm-5.3", 51, prompt=1864, completion=6, cost="0.000002"),
        ]
        attribute_costs(_run([cell]), rows)
        self.assertEqual(cell["cost_usdc"], "0.000002")

    def test_legacy_usage_all_cell_still_sums(self) -> None:
        cell = _cell("zai/glm-5.3", "cache", {"prompt_tokens": 1864, "completion_tokens": 6})
        cell["evidence"]["usage_all"] = [
            {"prompt_tokens": 1864, "completion_tokens": 6},
            {"prompt_tokens": 1864, "completion_tokens": 6},
        ]
        rows = [
            _row("zai/glm-5.3", 51, prompt=1864, completion=6, cost="0.000002"),
            _row("zai/glm-5.3", 52, prompt=1864, completion=6, cost="0.000002"),
        ]
        attribute_costs(_run([cell]), rows)
        self.assertEqual(cell["cost_usdc"], "0.000004")

    def test_order_fallback_only_without_interference(self) -> None:
        core = _cell("zai/glm-5.3", "core")
        cache = _cell("zai/glm-5.3", "cache")
        rows = [
            _row("zai/glm-5.3", 51, cost="0.000100"),
            _row("zai/glm-5.3", 53, cost="0.000300"),
        ]
        attribute_costs(_run([core, cache]), rows)
        self.assertEqual(core["cost_usdc"], "0.000100")
        self.assertEqual(cache["cost_usdc"], "0.000300")
        self.assertEqual(core["cost_match"], "order")

    def test_order_fallback_skipped_when_extra_rows(self) -> None:
        core = _cell("zai/glm-5.3", "core")
        rows = [
            _row("zai/glm-5.3", 51, cost="0.000100"),
            _row("zai/glm-5.3", 52, cost="0.900000"),  # fleet traffic, not ours
        ]
        attribute_costs(_run([core]), rows)
        self.assertNotIn("cost_usdc", core)

    def test_rows_outside_window_ignored(self) -> None:
        cell = _cell("zai/glm-5.3", "cache", {"prompt_tokens": 70, "completion_tokens": 18})
        rows = [_row("zai/glm-5.3", 10, prompt=70, completion=18, cost="0.000013")]
        summary = attribute_costs(_run([cell]), rows)
        self.assertNotIn("cost_usdc", cell)
        self.assertEqual(summary["total_usdc"], "0.000000")

    def test_other_model_rows_ignored(self) -> None:
        cell = _cell("zai/glm-5.3", "core")
        rows = [_row("cp/xai/grok-4.6", 51, cost="0.000100")]
        attribute_costs(_run([cell]), rows)
        self.assertNotIn("cost_usdc", cell)

    def test_alias_models_variant_pool(self) -> None:
        cell = _cell(
            "glm-5.3", "cache", {"prompt_tokens": 70, "completion_tokens": 18}
        )
        rows = [
            _row("cb/glm-5.3", 51, prompt=999, completion=1, cost="0.500000"),
            _row("cb/glm-5.3", 53, prompt=70, completion=18, cost="0.000021"),
        ]
        summary = attribute_costs(
            _run([cell], aliases=["glm-5.3"]),
            rows,
            alias_models={"glm-5.3": ["cb/glm-5.3"]},
        )
        self.assertEqual(cell["cost_usdc"], "0.000021")
        self.assertEqual(summary["total_usdc"], "0.000021")

    def test_summary_counts(self) -> None:
        a = _cell("zai/glm-5.3", "core")
        b = _cell("zai/glm-5.3", "cache", {"prompt_tokens": 70, "completion_tokens": 18})
        rows = [
            _row("zai/glm-5.3", 51, cost="0.000100"),
            _row("zai/glm-5.3", 53, prompt=70, completion=18, cost="0.000013"),
        ]
        summary = attribute_costs(_run([a, b]), rows)
        self.assertEqual(summary["matched"], 2)
        self.assertEqual(summary["cells"], 2)
        self.assertEqual(summary["source"], "usage-logs")


class _FakeResp:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *exc) -> bool:
        return False


def _http_429(retry_after: str | None = None):
    import urllib.error

    headers = {}
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError(
        "https://inferhub.dev/api/usage/logs", 429, "Too Many Requests", headers, None
    )


class RateLimitRetryTests(unittest.TestCase):
    def test_retry_after_parsed_clamped_and_defaulted(self) -> None:
        from probe.costs import (
            RETRY_AFTER_CAP_S,
            RETRY_AFTER_DEFAULT_S,
            _retry_after_s,
        )

        self.assertEqual(_retry_after_s(_http_429("12")), 12.0)
        self.assertEqual(_retry_after_s(_http_429("0.5")), 1.0)  # floored
        self.assertEqual(_retry_after_s(_http_429("999")), RETRY_AFTER_CAP_S)
        self.assertEqual(_retry_after_s(_http_429(None)), RETRY_AFTER_DEFAULT_S)
        self.assertEqual(_retry_after_s(_http_429("junk")), RETRY_AFTER_DEFAULT_S)

    def test_get_json_retries_429_then_succeeds(self) -> None:
        import json
        from unittest import mock

        import probe.costs as costs

        ok_body = {"rows": [{"ts": "2026-08-28T00:00:00Z"}]}
        calls = {"n": 0}

        def fake_urlopen(req, timeout=30):
            calls["n"] += 1
            if calls["n"] == 1:
                raise _http_429("12")
            return _FakeResp(json.dumps(ok_body).encode())

        with mock.patch.object(costs.urllib.request, "urlopen", side_effect=fake_urlopen), \
                mock.patch.object(costs.time, "sleep") as sleep_mock:
            result = costs._get_json("https://inferhub.dev/api/usage/logs?page=1", "k")

        self.assertEqual(result, ok_body)
        self.assertEqual(calls["n"], 2)
        sleep_mock.assert_called_once_with(12.0)

    def test_get_json_gives_up_after_max_retries(self) -> None:
        import urllib.error
        from unittest import mock

        import probe.costs as costs

        def fake_urlopen(req, timeout=30):
            raise _http_429("1")

        with mock.patch.object(costs.urllib.request, "urlopen", side_effect=fake_urlopen), \
                mock.patch.object(costs.time, "sleep"):
            with self.assertRaises(urllib.error.HTTPError):
                costs._get_json("https://inferhub.dev/api/usage/logs?page=1", "k")

    def test_fetch_log_rows_paces_between_pages(self) -> None:
        from unittest import mock

        import probe.costs as costs

        # Two full pages then an empty third, with a known rangeTotal.
        page_bodies = {
            1: {"rows": [{"ts": "2026-08-28T00:00:02Z"}] * costs.PAGE_SIZE,
                "rangeTotal": str(costs.PAGE_SIZE * 2)},
            2: {"rows": [{"ts": "2026-08-28T00:00:01Z"}] * costs.PAGE_SIZE,
                "rangeTotal": str(costs.PAGE_SIZE * 2)},
        }

        def fake_get_json(url, key):
            page = int(url.split("page=")[1])
            return page_bodies.get(page, {"rows": []})

        with mock.patch.object(costs, "_get_json", side_effect=fake_get_json), \
                mock.patch.object(costs.time, "sleep") as sleep_mock:
            rows = costs.fetch_log_rows("k", range_="30d", pace_s=0.25)

        self.assertEqual(len(rows), costs.PAGE_SIZE * 2)
        # One pacing sleep between page 1 -> 2 (loop ends at page 2, full page).
        self.assertEqual(sleep_mock.call_count, 1)
        sleep_mock.assert_called_with(0.25)


if __name__ == "__main__":
    unittest.main()
