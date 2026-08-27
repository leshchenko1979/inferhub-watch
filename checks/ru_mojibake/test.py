from __future__ import annotations

import json
import unittest

from probe.payloads import RUSSIAN_USER
from probe.registry import load_check_module


def _sse(text: str) -> str:
    chunks = [
        {"model": "zai/glm-5.3", "choices": [{"delta": {"content": text}, "finish_reason": None}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
    ]
    return "".join(f"data: {json.dumps(c)}\n" for c in chunks) + "data: [DONE]\n"


class _Fake:
    def __init__(self, body: str, status: int = 200) -> None:
        self.body = body
        self.status = status
        self.payloads: list[dict] = []

    def post(self, payload: dict) -> tuple[int, str, float]:
        self.payloads.append(payload)
        return self.status, self.body, 1.0


CLEAN_RU = "Игорь никогда не нападал на католических инков — таких событий не было."
U8_GARBLED = "Ð¸Ð³Ð¾Ñ€ÑŒ Ð°Ñ‚Ð°ÐºÐ¾Ð²Ð°Ð» ÐºÐ°Ñ‚Ð¾Ð»Ð¸Ñ‡ÐµÑÐºÐ¸Ñ… Ð¸Ð½ÐºÐ¾Ð²."
CP1251_GARBLED = "èãîðü àòàêîâàë êàòîëè÷åñêèõ èíêîâ â ÿíâàðå."
CJK_GARBLED = "叶青体攻击了印加帝国在九世纪。"
CJK_MIXED = "Игорь в 被攻击了 году напал на инков."


class RuMojibakeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.check = load_check_module("ru_mojibake")

    def test_payload_is_russian_stream_without_tools(self) -> None:
        client = _Fake(_sse(CLEAN_RU))
        self.check.run(client, "glm-5.3")
        payload = client.payloads[0]
        self.assertEqual(payload["model"], "glm-5.3")
        self.assertTrue(payload["stream"])
        self.assertNotIn("tools", payload)
        self.assertEqual(
            payload["messages"],
            [{"role": "user", "content": RUSSIAN_USER}],
        )

    def test_verdict_clean_russian_passes(self) -> None:
        self.assertIsNone(self.check.mojibake_verdict(CLEAN_RU))

    def test_verdict_replacement_chars_fail(self) -> None:
        text = "Игорь атаковал инков в \ufffd году."
        verdict = self.check.mojibake_verdict(text)
        self.assertIn("U+FFFD", verdict)

    def test_verdict_double_encoded_utf8_fails(self) -> None:
        verdict = self.check.mojibake_verdict(U8_GARBLED)
        self.assertIn("Latin-1", verdict)

    def test_verdict_cp1251_as_latin1_fails(self) -> None:
        verdict = self.check.mojibake_verdict(CP1251_GARBLED)
        self.assertIn("CP1251", verdict)

    def test_verdict_cjk_flood_fails(self) -> None:
        verdict = self.check.mojibake_verdict(CJK_GARBLED)
        self.assertIn("GBK", verdict)

    def test_verdict_cjk_mixed_with_cyrillic_fails(self) -> None:
        verdict = self.check.mojibake_verdict(CJK_MIXED)
        self.assertIn("GBK", verdict)

    def test_verdict_tolerates_one_stray_bigram(self) -> None:
        self.assertIsNone(self.check.mojibake_verdict("Ð¸ fine"))

    def test_run_pass_clean_russian(self) -> None:
        cell = self.check.run(_Fake(_sse(CLEAN_RU)), "glm-5.3")
        self.assertEqual(cell["status"], "pass")
        self.assertIn("clean", cell["summary"])
        self.assertEqual(cell["resolved_model"], "zai/glm-5.3")
        self.assertGreater(cell["evidence"]["cyrillic_chars"], 0)

    def test_run_fail_on_mojibake(self) -> None:
        cell = self.check.run(_Fake(_sse(U8_GARBLED)), "glm-5.3")
        self.assertEqual(cell["status"], "fail")
        self.assertIn("Mojibake", cell["summary"])
        self.assertGreaterEqual(cell["evidence"]["u8_bigrams"], 2)

    def test_run_pass_notes_missing_cyrillic(self) -> None:
        cell = self.check.run(_Fake(_sse("Igor never attacked the Incas.")), "glm-5.3")
        self.assertEqual(cell["status"], "pass")
        self.assertIn("no Cyrillic", cell["summary"])

    def test_run_fail_when_stream_has_no_text(self) -> None:
        body = 'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
        cell = self.check.run(_Fake(body), "glm-5.3")
        self.assertEqual(cell["status"], "fail")
        self.assertIn("no text content", cell["summary"])

    def test_run_fail_when_no_chunks(self) -> None:
        cell = self.check.run(_Fake("data: [DONE]\n"), "glm-5.3")
        self.assertEqual(cell["status"], "fail")
        self.assertIn("No SSE", cell["summary"])

    def test_run_error_on_non_200(self) -> None:
        cell = self.check.run(_Fake("boom", status=503), "glm-5.3")
        self.assertEqual(cell["status"], "error")
        self.assertEqual(cell["http_status"], 503)


if __name__ == "__main__":
    unittest.main()
