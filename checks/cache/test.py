"""Canned-SSE fixtures for the cache twin: one byte-identical repeat of the
core payload, pass iff cached_tokens > 0 on that first repeat. No retries, no
prefix request — that is the suite-v2 contract."""

from __future__ import annotations

import json
import unittest

from probe.registry import load_check_module


def _sse(chunks: list[dict]) -> str:
    return "".join(f"data: {json.dumps(c)}\n" for c in chunks) + "data: [DONE]\n"


class _Fake:
    """Records the payload it was handed and replays one canned body."""

    def __init__(self, body: str, status: int = 200) -> None:
        self.body = body
        self.status = status
        self.payload: dict = {}

    def post(self, payload: dict) -> tuple[int, str, float]:
        self.payload = payload
        return self.status, self.body, 1.0


def _chunks(*, cached: int = 0) -> list[dict]:
    return [
        {"choices": [{"delta": {"content": "ok"}, "finish_reason": None}]},
        {
            "choices": [{"delta": {}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": 100,
                "prompt_tokens_details": {"cached_tokens": cached},
            },
        },
    ]


class CacheCheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.check = load_check_module("cache")

    def test_repeat_is_the_core_payload(self) -> None:
        client = _Fake(_sse(_chunks(cached=80)))
        self.check.run(client, "zai/glm-5.3")
        payload = client.payload
        self.assertTrue(payload["stream"])
        self.assertEqual(payload["tool_choice"], "required")
        self.assertEqual(payload["tools"][0]["function"]["name"], "report_answer")
        # suite v2: the cache twin sends the identical request, no tools stripped
        self.assertIn("tools", payload)

    def test_cache_hit_passes(self) -> None:
        out = self.check.run(_Fake(_sse(_chunks(cached=80))), "zai/glm-5.3")
        self.assertEqual(out["status"], "pass")
        self.assertIn("Prompt cache hit", out["summary"])
        self.assertEqual(out["evidence"]["cached_tokens"], 80)
        self.assertEqual(out["evidence"]["prompt_tokens"], 100)
        self.assertAlmostEqual(out["evidence"]["hit_ratio"], 0.8)

    def test_cache_miss_fails(self) -> None:
        out = self.check.run(_Fake(_sse(_chunks(cached=0))), "zai/glm-5.3")
        self.assertEqual(out["status"], "fail")
        self.assertIn("cached_tokens", out["summary"])

    def test_no_sse_chunks_fails(self) -> None:
        out = self.check.run(_Fake("data: [DONE]\n"), "zai/glm-5.3")
        self.assertEqual(out["status"], "fail")
        self.assertIn("No SSE JSON chunks", out["summary"])

    def test_http_error_cell(self) -> None:
        out = self.check.run(_Fake("gateway exploded", status=500), "zai/glm-5.3")
        self.assertEqual(out["status"], "error")
        self.assertIn("500", out["summary"])


if __name__ == "__main__":
    unittest.main()