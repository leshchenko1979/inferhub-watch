"""Canned-SSE fixtures for the cache twin: one byte-identical repeat of the
core prompt, pass iff cached_tokens > 0 on that first repeat. No content
retries, no prefix request — that is the suite-v2 contract. The twin requests
streamed usage via stream_options; a route that 400s the param gets ONE
fallback retry without it (the max_tokens precedent)."""

from __future__ import annotations

import json
import unittest

from probe.registry import load_check_module


def _sse(chunks: list[dict]) -> str:
    return "".join(f"data: {json.dumps(c)}\n" for c in chunks) + "data: [DONE]\n"


class _Fake:
    """Records every payload it was handed and replays canned bodies.

    With a single (body, status) it replays that body on every post, so the
    existing single-request tests behave exactly as before. Pass a
    ``responses`` list of (status, body) to script the 400→fallback path.
    """

    def __init__(
        self,
        body: str,
        status: int = 200,
        responses: list[tuple[int, str]] | None = None,
    ) -> None:
        self.payload: dict = {}
        self.payloads: list[dict] = []
        self.calls = 0
        self._script = list(responses) if responses is not None else [(status, body)]

    def post(self, payload: dict) -> tuple[int, str, float]:
        self.payload = payload
        self.payloads.append(payload)
        idx = min(self.calls, len(self._script) - 1)
        status, body = self._script[idx]
        self.calls += 1
        return status, body, 1.0


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
        # usage requested explicitly — spec-strict upstreams stay silent otherwise
        self.assertEqual(payload["stream_options"], {"include_usage": True})

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

    def test_400_retries_without_usage_and_flags_it(self) -> None:
        client = _Fake(
            "",
            responses=[(400, "stream_options not supported"), (200, _sse(_chunks(cached=80)))],
        )
        out = self.check.run(client, "zai/glm-5.3")
        self.assertEqual(client.calls, 2)
        # first attempt asked for usage, the retry dropped it
        self.assertEqual(client.payloads[0]["stream_options"], {"include_usage": True})
        self.assertNotIn("stream_options", client.payloads[1])
        self.assertEqual(out["status"], "pass")
        self.assertIs(out["evidence"]["usage_requested"], False)

    def test_400_twice_errors_out_after_one_retry(self) -> None:
        client = _Fake("", responses=[(400, "nope"), (400, "still nope")])
        out = self.check.run(client, "zai/glm-5.3")
        self.assertEqual(client.calls, 2)
        self.assertEqual(out["status"], "error")
        self.assertIn("400", out["summary"])

    def test_usage_requested_flag_true_on_clean_pass(self) -> None:
        out = self.check.run(_Fake(_sse(_chunks(cached=80))), "zai/glm-5.3")
        self.assertIs(out["evidence"]["usage_requested"], True)


if __name__ == "__main__":
    unittest.main()