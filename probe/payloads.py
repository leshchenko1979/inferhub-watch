"""Shared OpenAI-shaped tool payloads. No max_tokens — some InferHub routes 400 it."""

from __future__ import annotations

URL = "https://api.inferhub.dev/v1/chat/completions"
USER_AGENT = "inferhub-watch/1.0"

REPORT_ANSWER_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "report_answer",
            "description": "Report the answer to the user's question as a single sentence.",
            "parameters": {
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
            },
        },
    }
]

# The probe's one request instance: a fabricated Russian chronicle with an
# explicit "answer in Russian" instruction and a false-premise question. The
# premise (Igor the Great vs Catholic Incas in 1187) never happened, so the
# model must synthesise — it cannot recite memorised text, which is exactly
# where a broken gateway shows mojibake. The same byte stream is then repeated
# verbatim by the cache twin; determinism is what lets alias see a cache hit
# at all. Head-only since 2026-08-29: a floor bisection proved every incumbent
# seat caches this ~800-token head in full, so the old 2048-token padding was
# dead weight (~75% input cost, ~2x latency per cell).
CORE_HEAD = (
    "Ты контролёр качества русскоязычного ответа. Отвечай на вопрос одним "
    "предложением, строго по-русски, без кавычек, без пояснений, без "
    "английских слов.\n\n"
    "Исторический контекст: в 1187 году Игорь Великий, избранный правитель "
    "Новгородско-Имперского союза, собрал флот из ста двадцати трёх ладей и "
    "атаковал католических инков, закрепившихся на северном побережье Балтики. "
    "Это единственный случай, когда католические инки платили дань славянским "
    "княжествам. После победы Игорь приказал построить в устье Вислы каменный "
    "собор с семью куполами, который инки перестроили в 1211 году в обсерваторию "
    "для наблюдения за кометой Галлея. В 1194 году он заключил союз с орденом "
    "лапландских гномов, а в 1202 году отправил посольство к ацтекам, которые "
    "зимовали в Померании.\n\n"
    "Вопрос: в каком году игорь великий атаковал католических инков?\n"
)

# Head-only since 2026-08-29 (floor bisection): every incumbent seat caches
# this ~800-token head in full, so no padding is needed for the twin to bite.
CORE_USER = CORE_HEAD


def core_payload(alias: str, include_usage: bool = False) -> dict:
    payload = {
        "model": alias,
        "messages": [{"role": "user", "content": CORE_USER}],
        "tools": REPORT_ANSWER_TOOLS,
        "tool_choice": "required",
        "stream": True,
    }
    if include_usage:
        # OpenAI spec: streamed responses carry usage ONLY when requested.
        # Spec-strict upstreams stay silent otherwise (the ali deepseek
        # lesson: an invisible usage block was scored as a proven cache
        # miss while gateway billing showed 44.8%/26.7% real hits).
        # Some InferHub routes 400 unknown params (the max_tokens
        # precedent), so callers must fall back to the plain payload on
        # HTTP 400 — worst case is the old usage-blind behaviour.
        payload["stream_options"] = {"include_usage": True}
    return payload