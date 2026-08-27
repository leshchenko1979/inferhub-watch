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
        "checks": ["stream_tools", "cache_tools", "ru_mojibake", "usage_pricing"],
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
        cell = _cell("zai/glm-5.3", "usage_pricing", {"prompt_tokens": 70, "completion_tokens": 18})
        rows = [
            _row("zai/glm-5.3", 51, prompt=999, completion=888, cost="0.500000"),
            _row("zai/glm-5.3", 53, prompt=70, completion=18, cost="0.000013"),
        ]
        summary = attribute_costs(_run([cell]), rows)
        self.assertEqual(cell["cost_usdc"], "0.000013")
        self.assertEqual(cell["cost_match"], "tokens")
        self.assertEqual(summary["total_usdc"], "0.000013")

    def test_multi_request_cell_sums_identical_rows(self) -> None:
        cell = _cell("zai/glm-5.3", "cache_tools", {"prompt_tokens": 1864, "completion_tokens": 6})
        cell["evidence"]["usage_all"] = [
            {"prompt_tokens": 1864, "completion_tokens": 6},
        ] * 3
        rows = [
            _row("zai/glm-5.3", 51, prompt=1864, completion=6, cost="0.000002"),
            _row("zai/glm-5.3", 52, prompt=1864, completion=6, cost="0.000002"),
            _row("zai/glm-5.3", 53, prompt=1864, completion=6, cost="0.000002"),
        ]
        attribute_costs(_run([cell]), rows)
        self.assertEqual(cell["cost_usdc"], "0.000006")

    def test_order_fallback_only_without_interference(self) -> None:
        stream = _cell("zai/glm-5.3", "stream_tools")
        cache = _cell("zai/glm-5.3", "cache_tools")
        rows = [
            _row("zai/glm-5.3", 51, cost="0.000100"),
            _row("zai/glm-5.3", 53, cost="0.000300"),
        ]
        attribute_costs(_run([stream, cache]), rows)
        self.assertEqual(stream["cost_usdc"], "0.000100")
        self.assertEqual(cache["cost_usdc"], "0.000300")
        self.assertEqual(stream["cost_match"], "order")

    def test_order_fallback_skipped_when_extra_rows(self) -> None:
        stream = _cell("zai/glm-5.3", "stream_tools")
        rows = [
            _row("zai/glm-5.3", 51, cost="0.000100"),
            _row("zai/glm-5.3", 52, cost="0.900000"),  # fleet traffic, not ours
        ]
        attribute_costs(_run([stream]), rows)
        self.assertNotIn("cost_usdc", stream)

    def test_rows_outside_window_ignored(self) -> None:
        cell = _cell("zai/glm-5.3", "usage_pricing", {"prompt_tokens": 70, "completion_tokens": 18})
        rows = [_row("zai/glm-5.3", 10, prompt=70, completion=18, cost="0.000013")]
        summary = attribute_costs(_run([cell]), rows)
        self.assertNotIn("cost_usdc", cell)
        self.assertEqual(summary["total_usdc"], "0.000000")

    def test_other_model_rows_ignored(self) -> None:
        cell = _cell("zai/glm-5.3", "stream_tools")
        rows = [_row("cp/xai/grok-4.6", 51, cost="0.000100")]
        attribute_costs(_run([cell]), rows)
        self.assertNotIn("cost_usdc", cell)

    def test_alias_models_variant_pool(self) -> None:
        cell = _cell(
            "glm-5.3", "usage_pricing", {"prompt_tokens": 70, "completion_tokens": 18}
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
        a = _cell("zai/glm-5.3", "stream_tools")
        b = _cell("zai/glm-5.3", "usage_pricing", {"prompt_tokens": 70, "completion_tokens": 18})
        rows = [
            _row("zai/glm-5.3", 51, cost="0.000100"),
            _row("zai/glm-5.3", 53, prompt=70, completion=18, cost="0.000013"),
        ]
        summary = attribute_costs(_run([a, b]), rows)
        self.assertEqual(summary["matched"], 2)
        self.assertEqual(summary["cells"], 2)
        self.assertEqual(summary["source"], "usage-logs")


if __name__ == "__main__":
    unittest.main()
