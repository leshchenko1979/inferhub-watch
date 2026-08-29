"""Canned-SSE fixtures for the core check's sub-assertions.

One request must assert the stream shape as the consuming runtime parses it (finish_reason
"" fails, empty-name deltas are tolerated), a named report_answer tool
call, and a clean Russian answer — text content OR the tool argument (several
routes emit zero text under tool_choice: "required"). Each failing
sub-assertion is covered here with its own fixture stream."""

from __future__ import annotations

import json
import unittest

from probe.payloads import CORE_USER
from probe.registry import load_check_module
from probe.sse import inspect_stream


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


ANSWER = "Игорь Великий атаковал католических инков в 1187 году."
ANSWER_JSON = json.dumps({"answer": ANSWER}, ensure_ascii=False)


def _tool_chunks(*, content: str = "", args: str = ANSWER_JSON, arg_split: int = -1) -> list[dict]:
    """A clean stream: one named tool, optional content, split argument JSON."""
    chunks: list[dict] = [
        {"choices": [{"delta": {"content": content}, "finish_reason": None}]},
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "c1",
                                "function": {"name": "report_answer", "arguments": ""},
                            }
                        ]
                    },
                    "finish_reason": None,
                }
            ]
        },
    ]
    if arg_split < 0:
        arg_split = len(args) // 2
    chunks.append(
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "function": {"arguments": args[:arg_split]}}
                        ]
                    },
                    "finish_reason": None,
                }
            ]
        }
    )
    chunks.append(
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "function": {"arguments": args[arg_split:]}}
                        ]
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
    )
    return chunks


class CoreCheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.check = load_check_module("core")

    def test_payload_is_the_shared_core_shape(self) -> None:
        client = _Fake(_sse(_tool_chunks()))
        self.check.run(client, "zai/glm-5.3")
        payload = client.payload
        self.assertTrue(payload["stream"])
        self.assertEqual(payload["tool_choice"], "required")
        self.assertEqual(payload["tools"][0]["function"]["name"], "report_answer")
        self.assertEqual(payload["messages"][0]["role"], "user")
        # Byte-identical content is the cache twin's invariant; size went
        # head-only with the 2026-08-29 floor bisection.
        self.assertEqual(payload["messages"][0]["content"], CORE_USER)
        self.assertNotIn("max_tokens", payload)

    def test_pass_with_text_content_answer(self) -> None:
        chunks = _tool_chunks(content=ANSWER, args=json.dumps({"answer": ""}))
        out = self.check.run(_Fake(_sse(chunks)), "zai/glm-5.3")
        self.assertEqual(out["status"], "pass")
        self.assertIn("as text content", out["summary"])

    def test_pass_with_tool_argument_only_russian(self) -> None:
        # Zero text content — the answer lives only in the streamed tool arg.
        chunks = _tool_chunks()
        out = self.check.run(_Fake(_sse(chunks)), "zai/glm-5.3")
        self.assertEqual(out["status"], "pass")
        self.assertIn("in tool arguments", out["summary"])
        self.assertEqual(out["evidence"]["cyrillic_chars"] > 0, True)
        self.assertEqual(out["evidence"]["content_chars"], 0)
        self.assertGreater(out["evidence"]["argument_chars"], 0)

    def test_pass_with_split_argument_json(self) -> None:
        # Fragments land mid-string and mid-escape: parse must still recover.
        args = json.dumps({"answer": "Ответ: Игорь атаковал в 1187 году."}, ensure_ascii=False)
        chunks = _tool_chunks(args=args, arg_split=3)
        out = self.check.run(_Fake(_sse(chunks)), "zai/glm-5.3")
        self.assertEqual(out["status"], "pass")

    def test_no_russian_text_fails(self) -> None:
        chunks = _tool_chunks(content="", args="")
        out = self.check.run(_Fake(_sse(chunks)), "zai/glm-5.3")
        self.assertEqual(out["status"], "fail")
        self.assertIn("Ordered Russian text, got none", out["summary"])

    def test_mojibake_answer_fails(self) -> None:
        content = "Игорь \ufffd\ufffd\ufffd напал на инков"
        chunks = _tool_chunks(content=content, args=json.dumps({"answer": ""}))
        out = self.check.run(_Fake(_sse(chunks)), "zai/glm-5.3")
        self.assertEqual(out["status"], "fail")
        self.assertIn("Mojibake", out["summary"])
        self.assertIn("replacement", out["summary"])

    def test_no_cyrillic_answer_fails(self) -> None:
        content = "The Incas were attacked in 1187."
        chunks = _tool_chunks(content=content, args=json.dumps({"answer": ""}))
        out = self.check.run(_Fake(_sse(chunks)), "zai/glm-5.3")
        self.assertEqual(out["status"], "fail")
        self.assertIn("No Cyrillic", out["summary"])

    def test_empty_finish_reason_fails_shape(self) -> None:
        chunks = [
            {
                "choices": [
                    {"delta": {"content": ANSWER}, "finish_reason": ""}
                ]
            }
        ]
        stats = inspect_stream(chunks)
        self.assertGreater(stats["empty_finish_chunks"], 0)
        out = self.check.run(_Fake(_sse(chunks)), "zai/glm-5.3")
        self.assertEqual(out["status"], "fail")
        self.assertIn("finish_reason", out["summary"])
        self.assertIn("terminal", out["summary"])

    def test_empty_name_deltas_tolerated_oc_parity(self) -> None:
        # OC parity: the accumulator skips name "" deltas (first non-empty
        # name sticks) — ali/qwencloud gateways stream exactly this shape.
        chunks = [
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "c1",
                                    "function": {"name": "report_answer", "arguments": ""},
                                }
                            ]
                        },
                        "finish_reason": None,
                    }
                ]
            },
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "function": {"name": "", "arguments": ANSWER_JSON},
                                }
                            ]
                        },
                        "finish_reason": None,
                    }
                ]
            },
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ]
        stats = inspect_stream(chunks)
        self.assertGreater(stats["empty_name_chunks"], 0)
        self.assertEqual(stats["names"], ["report_answer"])
        out = self.check.run(_Fake(_sse(chunks)), "ali/deepseek-v4-flash-0731")
        self.assertEqual(out["status"], "pass")

    def test_only_empty_tool_names_fails(self) -> None:
        chunks = [
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "c1",
                                    "function": {"name": "", "arguments": ""},
                                }
                            ]
                        },
                        "finish_reason": None,
                    }
                ]
            }
        ]
        stats = inspect_stream(chunks)
        self.assertGreater(stats["empty_name_chunks"], 0)
        out = self.check.run(_Fake(_sse(chunks)), "zai/glm-5.3")
        self.assertEqual(out["status"], "fail")
        self.assertIn("non-empty tool name", out["summary"])

    def test_no_named_tool_fails(self) -> None:
        chunks = [
            {"choices": [{"delta": {"content": ANSWER}, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ]
        out = self.check.run(_Fake(_sse(chunks)), "zai/glm-5.3")
        self.assertEqual(out["status"], "fail")
        self.assertIn("non-empty tool name", out["summary"])

    def test_http_error_cell(self) -> None:
        out = self.check.run(_Fake("gateway exploded", status=500), "zai/glm-5.3")
        self.assertEqual(out["status"], "error")
        self.assertIn("500", out["summary"])


if __name__ == "__main__":
    unittest.main()