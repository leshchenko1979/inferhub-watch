"""Unit tests for Candidate Sustained TPS Qualification Probe (Issue #22)."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from probe.tps_qual import (
    build_qual_payload,
    load_qual_runs,
    parse_stream_metrics,
    record_qualification_result,
    run_candidate_tps_qual,
    save_qual_runs,
)


def _make_mock_sse(words: list[str], completion_tokens: int = 500, reasoning_tokens: int = 0) -> str:
    lines = []
    for w in words:
        chunk = {"choices": [{"delta": {"content": w + " "}}]}
        lines.append(f"data: {json.dumps(chunk)}\n")
    # Add usage chunk
    usage_chunk = {
        "choices": [{"delta": {}}],
        "usage": {
            "prompt_tokens": 50,
            "completion_tokens": completion_tokens,
            "completion_tokens_details": {
                "reasoning_tokens": reasoning_tokens,
            },
        },
    }
    lines.append(f"data: {json.dumps(usage_chunk)}\n")
    lines.append("data: [DONE]\n")
    return "\n".join(lines)


def test_build_qual_payload():
    p = build_qual_payload("test/model", max_tokens=800)
    assert p["model"] == "test/model"
    assert p["stream"] is True
    assert p["max_tokens"] == 800
    assert len(p["messages"]) == 2
    assert "Raft" in p["messages"][1]["content"]


def test_parse_stream_metrics_standard():
    # 500 completion tokens, 0 reasoning, elapsed 10.0s, ttft 2.0s -> stream_dur 8.0s
    # tps = 500 / 8.0 = 62.5
    raw = _make_mock_sse(["word"] * 50, completion_tokens=500, reasoning_tokens=0)
    metrics = parse_stream_metrics(raw, elapsed_ms=10000.0, ttft_ms=2000.0)

    assert metrics["valid"] is True
    assert metrics["tps"] == 62.5
    assert metrics["visible_tokens"] == 500
    assert metrics["reasoning_tokens"] == 0
    assert metrics["stream_dur_s"] == 8.0


def test_parse_stream_metrics_with_reasoning_deduction():
    # Thinking model: 500 completion tokens, 200 reasoning tokens -> visible = 300
    # elapsed 6000ms, ttft 1000ms -> stream_dur 5.0s
    # tps = 300 / 5.0 = 60.0
    raw = _make_mock_sse(["word"] * 50, completion_tokens=500, reasoning_tokens=200)
    metrics = parse_stream_metrics(raw, elapsed_ms=6000.0, ttft_ms=1000.0)

    assert metrics["valid"] is True
    assert metrics["visible_tokens"] == 300
    assert metrics["reasoning_tokens"] == 200
    assert metrics["tps"] == 60.0


def test_parse_stream_metrics_burst_duration_rejection():
    # Duration < 2.0s -> reject as invalid / burst artifact
    raw = _make_mock_sse(["word"] * 50, completion_tokens=500, reasoning_tokens=0)
    metrics = parse_stream_metrics(raw, elapsed_ms=1500.0, ttft_ms=100.0)

    assert metrics["valid"] is False
    assert metrics["tps"] is None


def test_parse_stream_metrics_extreme_tps_bound_rejection():
    # 5000 tokens in 2.5s -> 2000 TPS (> 500 MAX_VALID_TPS) -> reject
    raw = _make_mock_sse(["word"] * 50, completion_tokens=5000, reasoning_tokens=0)
    metrics = parse_stream_metrics(raw, elapsed_ms=3000.0, ttft_ms=500.0)

    assert metrics["valid"] is False
    assert metrics["tps"] is None


def test_load_and_save_qual_runs(tmp_path):
    qual_file = tmp_path / "data" / "qual_runs.json"
    assert load_qual_runs(qual_file) == {"models": {}}

    data = {
        "models": {
            "test/m1": {"tps": 75.5, "visible_tokens": 450, "stream_dur_s": 5.96, "n": 1}
        }
    }
    save_qual_runs(data, qual_file)
    loaded = load_qual_runs(qual_file)
    assert loaded["models"]["test/m1"]["tps"] == 75.5


def test_record_qualification_result(tmp_path):
    qual_file = tmp_path / "data" / "qual_runs.json"
    metrics = {
        "tps": 88.2,
        "visible_tokens": 520,
        "reasoning_tokens": 0,
        "stream_dur_s": 5.9,
    }
    res1 = record_qualification_result("test/m2", metrics, qual_file)
    assert res1["n"] == 1
    assert res1["tps"] == 88.2

    # Second record increments n
    res2 = record_qualification_result("test/m2", metrics, qual_file)
    assert res2["n"] == 2


def test_run_candidate_tps_qual(tmp_path):
    qual_file = tmp_path / "data" / "qual_runs.json"
    raw_sse = _make_mock_sse(["hello", "world"], completion_tokens=400, reasoning_tokens=0)

    mock_client = MagicMock()
    mock_client.post.return_value = (200, raw_sse, 6000.0, 1000.0)

    res = run_candidate_tps_qual(mock_client, "cand/m3", qual_path=qual_file)
    assert res["valid"] is True
    assert res["tps"] == 80.0

    # Verify persisted in qual_file
    data = load_qual_runs(qual_file)
    assert "cand/m3" in data["models"]
    assert data["models"]["cand/m3"]["tps"] == 80.0
