# Core probe: stream shape, tool call, Russian answer in one request

InferHub advertises an OpenAI-compatible `/v1/chat/completions` stream. This check sends **one** request and scores three things at once, so a broken route fails on the first request and nothing after it is probed:

1. **stream shape** — the SSE deltas match the OpenAI convention;
2. **tool fired** — a non-empty tool name arrives on the stream;
3. **russian** — the answer comes back as clean Russian, in the `report_answer` tool argument if not as text.

OpenAI documents streaming chat completions and tool-call deltas here: [Chat Completions streaming](https://platform.openai.com/docs/api-reference/chat/streaming) and [function calling](https://platform.openai.com/docs/guides/function-calling).

## What we send

`stream: true`, `tool_choice: required`, one `report_answer` tool. The user message is a **fabricated Russian chronicle** (Igor the Great attacks the Catholic Incas in 1187): the head plus 12 deterministic pad blocks (**~1530 ali-billed tokens**, sized 2026-08-30 to clear ali's ~1024-token minimum cacheable prefix — the 2026-08-29 head-only shrink fell under it and ali's cache went dark; see the cache check's page for the spike numbers). The same byte stream is repeated verbatim by the cache check. We do **not** send `max_tokens`; some InferHub routes reject it. We request the **alias** in `model`; InferHub may return a different `model` string.

```json
{
  "model": "<alias>",
  "messages": [
    {
      "role": "user",
      "content": "<русская хроника целиком, ~1530 токенов: игорь великий, католические инки, 1187 год + 12 блоков пединга>"
    }
  ],
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "report_answer",
        "description": "Report the answer to the user's question as a single sentence.",
        "parameters": {
          "type": "object",
          "properties": {"answer": {"type": "string"}},
          "required": ["answer"]
        }
      }
    }
  ],
  "tool_choice": "required",
  "stream": true
}
```

The false premise — Igor never attacked "Catholic Incas" — forces the model to synthesise instead of reciting, which is where a broken gateway shows mojibake. Whether the model corrects the premise or plays along is **not** scored; a truthful correction is just a convenient long Cyrillic answer.

## Pass

All of:

- intermediate chunks use `finish_reason` of JSON `null` or omit it — a `"finish_reason": ""` chunk fails, because the consuming runtime treats any `finish_reason` as terminal and would flush tool calls mid-stream (empty/duplicated runs); tool-name deltas may carry `""` (the consumer skips them), as long as at least one non-empty name arrives;
- at least one named tool arrived;
- the answer (text content plus the `report_answer.answer` argument) decodes as readable Russian with no mojibake and at least one Cyrillic character.

## Fail

Any of:

- a chunk with `"finish_reason": ""` (terminal to the consumer's accumulator → mid-stream flush, empty/duplicated tool runs); a tool delta with `"name": ""` is tolerated evidence, not a failure;
- a required tool call with no non-empty name by the end of the stream;
- no text content and no parseable `answer` argument — "ordered Russian text, got none";
- U+FFFD replacement characters, double-encoded UTF-8 Cyrillic (`Ð¸Ð³Ð¾Ñ€ÑŒ`), CP1251-read-as-Latin-1, or a CJK flood in the answer;
- an answer with **no Cyrillic at all** — the prompt orders Russian text, so absence is a failure.

A non-empty but unusual `finish_reason` is stored in evidence and does not fail this check. Out of scope: truncation, JSON schema, parallel tools, other SDK behavior.