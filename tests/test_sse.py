"""Contract tests for probe.sse — the stream parsers the checks score by.

Canned payloads mirror real provider quirks seen in the wild: a malformed
JSON line mid-stream, an empty-string finish_reason, and empty-string tool
names under tool_choice="required"."""

from __future__ import annotations

import unittest

from probe.sse import (
    cached_tokens,
    finish_reason,
    inspect_stream,
    last_usage,
    parse_sse,
    resolved_model,
    tool_deltas,
    usage_pricing_fields,
)

RAW_STREAM = "\n".join(
    [
        ': comment line, ignored',
        'data: {"id":"1","model":"ali/qwen3.8-max","choices":[{"delta":{"tool_calls":[{"function":{"name":""}}]},"finish_reason":null}]}',
        '',
        'data: {not json — dropped, not fatal',
        'data: {"id":"2","choices":[{"delta":{"tool_calls":[{"function":{"name":"report_answer"}}]},"finish_reason":""}]}',
        'data: {"id":"3","choices":[{"delta":{},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":12,"completion_tokens":7}}',
        'data: [DONE]',
    ]
)


class ParseSseTest(unittest.TestCase):
    def test_keeps_data_lines_drops_done_and_garbage(self) -> None:
        chunks = parse_sse(RAW_STREAM)
        self.assertEqual([c["id"] for c in chunks], ["1", "2", "3"])

    def test_empty_input(self) -> None:
        self.assertEqual(parse_sse(""), [])


class ToolDeltasTest(unittest.TestCase):
    def test_streaming_delta(self) -> None:
        chunk = {"choices": [{"delta": {"tool_calls": [{"function": {"name": "a"}}]}}]}
        self.assertEqual(tool_deltas(chunk)[0]["function"]["name"], "a")

    def test_non_streaming_message(self) -> None:
        chunk = {"choices": [{"message": {"tool_calls": [{"function": {"name": "b"}}]}}]}
        self.assertEqual(tool_deltas(chunk)[0]["function"]["name"], "b")

    def test_no_choices(self) -> None:
        self.assertEqual(tool_deltas({}), [])
        self.assertEqual(tool_deltas({"choices": []}), [])


class FinishReasonTest(unittest.TestCase):
    def test_none_when_no_choices(self) -> None:
        self.assertIsNone(finish_reason({}))

    def test_empty_string_is_preserved(self) -> None:
        # "" is a broken-but-real provider behaviour; it must survive parsing.
        chunk = {"choices": [{"finish_reason": ""}]}
        self.assertEqual(finish_reason(chunk), "")


class ResolvedModelTest(unittest.TestCase):
    def test_first_non_empty_wins(self) -> None:
        chunks = [{"model": ""}, {"model": "ali/qwen3.8-max"}, {"model": "other"}]
        self.assertEqual(resolved_model(chunks), "ali/qwen3.8-max")

    def test_fallback(self) -> None:
        self.assertEqual(resolved_model([{"model": None}], "alias"), "alias")


class LastUsageTest(unittest.TestCase):
    def test_last_non_empty_block_wins(self) -> None:
        chunks = [{"usage": {"prompt_tokens": 1}}, {"usage": {}}, {"usage": {"prompt_tokens": 2}}]
        self.assertEqual(last_usage(chunks)["prompt_tokens"], 2)

    def test_absent(self) -> None:
        self.assertEqual(last_usage([{"id": "1"}]), {})


class InspectStreamTest(unittest.TestCase):
    def test_quirks_are_counted_and_named(self) -> None:
        stats = inspect_stream(parse_sse(RAW_STREAM))
        self.assertEqual(stats["chunk_count"], 3)
        self.assertEqual(stats["empty_name_chunks"], 1)
        self.assertEqual(stats["empty_finish_chunks"], 1)
        self.assertEqual(stats["names"], ["report_answer"])
        self.assertEqual(stats["last_finish_reason"], "tool_calls")

    def test_empty_stream(self) -> None:
        stats = inspect_stream([])
        self.assertEqual(stats["names"], [])
        self.assertIsNone(stats["last_finish_reason"])


class UsagePricingFieldsTest(unittest.TestCase):
    def test_only_present_keys_pass_through(self) -> None:
        usage = {"cost": "0.000100", "prompt_tokens": 12}
        out = usage_pricing_fields(usage)
        self.assertEqual(out, {"cost": "0.000100", "prompt_tokens": 12})

    def test_cached_tokens_lifted_from_details(self) -> None:
        usage = {"prompt_tokens_details": {"cached_tokens": 99}}
        self.assertEqual(usage_pricing_fields(usage)["cached_tokens"], 99)


class CachedTokensTest(unittest.TestCase):
    def test_alias_max_wins(self) -> None:
        usage = {"cached_tokens": 10, "prompt_cache_hit_tokens": 40}
        self.assertEqual(cached_tokens(usage), 40)

    def test_nested_details(self) -> None:
        usage = {"prompt_tokens_details": {"cache_read_input_tokens": 25}}
        self.assertEqual(cached_tokens(usage), 25)

    def test_garbage_counts_zero(self) -> None:
        usage = {"cached_tokens": True, "prompt_cache_hit_tokens": "many"}
        self.assertEqual(cached_tokens(usage), 0)


if __name__ == "__main__":
    unittest.main()
