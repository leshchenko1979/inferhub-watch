"""Shared OpenAI-shaped tool payloads. No max_tokens — some InferHub routes 400 it."""

from __future__ import annotations

URL = "https://api.inferhub.dev/v1/chat/completions"
USER_AGENT = "inferhub-watch/1.0"

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current weather for a city",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    }
]

SINGLE_USER = "Call get_weather for Paris. Do not answer in text. Use the tool."


def completion_payload(
    alias: str, *, stream: bool, system: str | None = None, with_tools: bool = True
) -> dict:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": SINGLE_USER})
    payload: dict = {
        "model": alias,
        "messages": messages,
        "stream": stream,
    }
    if with_tools:
        payload["tools"] = TOOLS
        payload["tool_choice"] = "required"
    return payload


CACHE_USER = "Reply with the single word paris. Nothing else."

# ClinePass can hit cache well below this; 2k is a modest floor, not a Cline session.
CACHE_PREFIX_MIN_TOKENS = 2048
_CACHE_CHARS_PER_TOKEN = 4


def approx_prompt_tokens(text: str) -> int:
    return max(len(text) // _CACHE_CHARS_PER_TOKEN, len(text.split()))


def cache_prefix(min_tokens: int | None = None, *, salt: str = "") -> str:
    target = CACHE_PREFIX_MIN_TOKENS if min_tokens is None else min_tokens
    head = (
        "You are a concise assistant. Follow the user. "
        "Keep answers to one word when asked. "
        "The same instructions apply on every turn.\n"
    )
    if salt:
        head += f"Unique probe salt: {salt}.\n"
    line = "Cache prefix line {:04d}: keep this system text identical on every retry.\n"
    parts = [head]
    n = 1
    while approx_prompt_tokens("".join(parts)) < target:
        parts.append(line.format(n))
        n += 1
    return "".join(parts)


def cache_payload(alias: str, system: str) -> dict:
    return {
        "model": alias,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": CACHE_USER},
        ],
        "stream": True,
    }


RUSSIAN_USER = "в каком году игорь великий атаковал католических инков?"


def russian_payload(alias: str, *, stream: bool = True) -> dict:
    return {
        "model": alias,
        "messages": [{"role": "user", "content": RUSSIAN_USER}],
        "stream": stream,
    }
