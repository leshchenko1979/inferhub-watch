# Mojibake in Russian answers

Non-ASCII prompts are where a broken gateway shows itself first. This check sends a **Russian** question and scores only one thing: whether the answer comes back as readable text or as mojibake.

## What we send

A plain streaming completion, no tools, no system prompt — just the user message, verbatim:

```json
{
  "model": "<alias>",
  "messages": [
    {
      "role": "user",
      "content": "в каком году игорь великий атаковал католических инков?"
    }
  ],
  "stream": true
}
```

The question is intentionally a trap: Igor never attacked "Catholic Incas" — the premise is an anachronism. Whether the model corrects the premise or plays along is **not** scored here; a truthful correction is just a convenient long Cyrillic answer.

## Pass

The answer decodes as readable text:

- no U+FFFD replacement characters (bytes were valid UTF-8),
- no double-encoded UTF-8 Cyrillic — `Ð`/`Ñ` followed by a Latin-1 tail, the classic `Ð¸Ð³Ð¾Ñ€ÑŒ` look,
- no CP1251-read-as-Latin-1 — Cyrillic turned into accented Latin like `èãîðü`,
- no East-Asian flood — UTF-8 bytes paired up by a GBK/Big5/Shift-JIS decoder produce Chinese like `叶青体` in place of the Cyrillic.

A clean answer that contains no Cyrillic at all (for example, an English reply) still passes this check, and the board note says so — that is a language-fidelity issue, not an encoding one.

## Fail

Any of:

- replacement characters in the answer,
- two or more CJK characters (Chinese, kana, or hangul) in the answer,
- two or more `Ð`+tail sequences,
- accented Latin-1 letters with zero Cyrillic (the CP1251 signature),
- a stream with no text content to score.

## Why it matters

Any user whose prompts are not pure ASCII. A route that mangles UTF-8 once will mangle every Cyrillic, CJK, or emoji-laden request — agents lose tool names, users see `Ñ€Ð°Ð·Ñ€Ð°Ð±Ð¾Ñ‚ÐºÐ°` or a page of Chinese. The failed shapes above are the real-world ways gateways corrupt Cyrillic: re-decoding UTF-8 bytes as Latin-1, re-decoding CP1251 bytes as Latin-1, and re-decoding UTF-8 bytes as GBK/Big5.
