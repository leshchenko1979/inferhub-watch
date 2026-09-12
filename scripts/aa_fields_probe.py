"""READ-ONLY: does the AA Free response actually carry TPS / TTFT?

The endpoint we already call each sweep
(`probe/intelligence.py::AA_URL`, the AA **Free** tier) is documented to
include median performance metrics. `_models_from_body` currently keeps only
name / iq / coding / aa_in / aa_out and discards the rest.

This script captures ONE live response and reports what is genuinely on the
wire — it writes NOTHING, changes NOTHING, and feeds nothing into Value.
Adopting AA's TPS would change WHAT the North Star measures (their prompt
presets vs our own streams), which is owner-gated.

It reports the discovered key set rather than testing assumed field names, so
a renamed field shows up as a discovery instead of a false negative.

Usage: python3 -m scripts.aa_fields_probe   (needs AA_API_KEY in the env)
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request

from probe.intelligence import AA_URL, USER_AGENT
from probe.registry import aa_slug

# The six routes HQ named, in rank order.
ROUTES = [
    "cb/deepseek-v4.1-flash",
    "ag/gemini-3.7-flash-high",
    "ag/gemini-3.8-flash-high",
    "cb/gpt-5.6-luna",
    "ali/qwen3.8-max",
    "cbcn/glm-5.3-flash",
]

# The two fields the Free-tier docs name. Checked as a CONFIRMATION of what
# the key scan discovers, never as the only thing we look for.
NAMED = ("median_output_tokens_per_second", "median_time_to_first_token_seconds")

# Substrings that would catch a renamed perf field.
HINTS = ("token", "second", "ttft", "time_to_first", "latency", "speed")


def perf_hint_counts(entries: list, max_depth: int = 3) -> dict[str, int]:
    """How many ENTRIES carry each perf-hint key, at any nesting depth.

    Counts entries, never occurrences: a key that nests twice inside one model
    is one entry, and a key present on every model reads as `len(entries)`.

    The list branch of the original walk descended `obj[0]` alone, so its
    figure was a statement about ONE model printed in the shape of a coverage
    count — it read as "1 of 646". `tests/test_aa_fields_probe.py` pins the
    scope.
    """
    counts: dict[str, int] = {}

    def scan(obj, hits: set, depth: int) -> None:
        if depth > max_depth:
            return
        if isinstance(obj, dict):
            for k, v in obj.items():
                if v is not None and any(h in k.lower() for h in HINTS):
                    hits.add(k)
                scan(v, hits, depth + 1)
        elif isinstance(obj, list):
            for item in obj:
                scan(item, hits, depth + 1)

    for entry in entries:
        hits: set = set()
        # Start at depth 1: an entry's own keys sit exactly where the old
        # `walk(entries[0], 1)` examined them, so the nesting reach is
        # unchanged — only the number of entries walked is.
        scan(entry, hits, 1)
        for k in hits:
            counts[k] = counts.get(k, 0) + 1
    return counts


def main() -> int:
    key = os.environ.get("AA_API_KEY")
    if not key:
        print("AA_API_KEY not set — this probe needs the CI-only key")
        return 2

    req = urllib.request.Request(
        AA_URL,
        headers={"x-api-key": key, "Accept": "application/json",
                 "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = json.load(resp)

    entries = body.get("data") or []
    print(f"AA URL: {AA_URL}")
    print(f"top-level keys: {sorted(body.keys())}")
    print(f"models on the wire: {len(entries)}")
    print()

    # 1. Union of top-level keys across entries — the discovery step.
    keys: set[str] = set()
    for e in entries:
        keys.update(e.keys())
    print("=== union of per-entry top-level keys ===")
    for k in sorted(keys):
        print(f"  {k}")
    print()

    # 2. Which of the documented perf fields are present, and on how many.
    print("=== named perf fields ===")
    for f in NAMED:
        n = sum(1 for e in entries if e.get(f) is not None)
        print(f"  {f:40} present on {n}/{len(entries)}")
    print()

    # 3. Any key that LOOKS like a perf metric, wherever it nests. The count
    #    is ENTRIES carrying the key, so it is directly comparable to the
    #    named-field coverage above.
    print("=== keys matching perf hints (any nesting depth) ===")
    found = perf_hint_counts(entries)
    for k in sorted(found):
        print(f"  {k:50} non-null on {found[k]}/{len(entries)} entries")
    if not found:
        print("  (none)")
    print()

    # 4. The values for our six routes.
    by_slug = {e.get("slug"): e for e in entries}
    print("=== our six ranked routes ===")
    for route in ROUTES:
        slug = aa_slug(route)
        e = by_slug.get(slug) if slug else None
        if e is None:
            print(f"  {route:32} slug={slug!r} — NOT on the wire")
            continue
        perf = {f: e.get(f) for f in NAMED}
        print(f"  {route:32} slug={slug}")
        for f, v in perf.items():
            print(f"      {f:40} {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
