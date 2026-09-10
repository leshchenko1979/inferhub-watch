#!/usr/bin/env python3
"""Offline portability and dry-run validator for inferhub-auto-route."""
from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def validate_package() -> dict[str, Any]:
    skill = ROOT / "SKILL.md"
    text = skill.read_text(encoding="utf-8")
    end = text.find("\n---\n", 4)
    if not text.startswith("---\n") or end < 0:
        raise ValueError("invalid YAML frontmatter delimiters")
    front = text[4:end]
    name = re.search(r"(?m)^name:\s*([a-z0-9-]+)$", front)
    description = re.search(r"(?m)^description:\s*\S", front)
    if not name or not description or name.group(1) != ROOT.name:
        raise ValueError("invalid name/description frontmatter")
    refs = re.findall(r"\]\((references/[^)]+)\)", text)
    missing = [ref for ref in refs if not (ROOT / ref).is_file()]
    if missing:
        raise ValueError(f"missing references: {missing}")
    if len(text.splitlines()) >= 500:
        raise ValueError("SKILL.md must stay below 500 lines")
    return {"name": name.group(1), "references": len(refs), "skill_lines": len(text.splitlines())}


def qualifies(route: str, record: dict[str, Any], iq_floor: float) -> bool:
    iq = record.get("iq")
    return (
        isinstance(iq, (int, float))
        and math.isfinite(iq)
        and iq >= iq_floor
        and record.get("supports_tools") is not False
        and isinstance(record.get("ask_in"), (int, float))
        and isinstance(record.get("ask_out"), (int, float))
        and record["ask_in"] > 0
        and record["ask_out"] > 0
    )


def dry_run(iq_floor: float = 35.0, input_weight: float = 0.99, output_weight: float = 0.01) -> dict[str, Any]:
    records = {
        "current/model": {"iq": 35.0, "ask_in": 0.004, "ask_out": 0.02, "supports_tools": True},
        "deepseek/deepseek-v4": {"iq": 36.0, "ask_in": 0.002, "ask_out": 0.01, "supports_tools": True},
        "weak/model": {"iq": 34.9, "ask_in": 0.001, "ask_out": 0.01, "supports_tools": True},
        "no-tools/model": {"iq": 50.0, "ask_in": 0.001, "ask_out": 0.01, "supports_tools": False},
    }
    qualified = [
        {"route": route, "value": rec["iq"] / (input_weight * rec["ask_in"] + output_weight * rec["ask_out"])}
        for route, rec in records.items()
        if qualifies(route, rec, iq_floor)
    ]
    qualified.sort(key=lambda item: item["value"], reverse=True)
    return {"dry_run": True, "qualified": qualified, "config_updated": False, "sessions_updated": 0}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = {"package": validate_package()}
    if args.dry_run:
        result["decision"] = dry_run()
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
