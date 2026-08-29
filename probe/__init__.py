"""InferHub probe machinery: HTTP client, payload builders, response parsers.

Shared by the two live checks (checks/core, checks/cache) and the site
generator. The parsers in sse.py define the stream contract this repo scores
providers against — their docstrings are the contract, and tests/test_sse.py
pins them to canned provider payloads."""

from __future__ import annotations

# Makes `python3 -m probe.run` work.
