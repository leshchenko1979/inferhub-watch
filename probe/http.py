from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from probe.payloads import URL, USER_AGENT

class InferHubClient:
    def __init__(self, api_key: str, timeout: int = 90) -> None:
        self.api_key = api_key
        self.timeout = timeout

    def post(self, payload: dict[str, Any]) -> tuple[int, str, float, float | None]:
        """POST one completion. Returns (status, raw, elapsed_ms, ttft_ms).

        The body is read line by line so TTFT — the time to the FIRST line
        of the response — is captured when the first line arrives, not when
        the whole stream has drained. ttft_ms is None on the HTTPError path
        (error bodies are buffered wholesale; there is no stream to time).
        """
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            URL,
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream, application/json",
                "User-Agent": USER_AGENT,
            },
            method="POST",
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                lines: list[str] = []
                ttft_ms: float | None = None
                for line in resp:
                    if ttft_ms is None:
                        ttft_ms = (time.monotonic() - started) * 1000
                    lines.append(line.decode("utf-8", "replace"))
                raw = "".join(lines)
                elapsed = (time.monotonic() - started) * 1000
                return resp.status, raw, elapsed, ttft_ms
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            elapsed = (time.monotonic() - started) * 1000
            return exc.code, raw, elapsed, None
