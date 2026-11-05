import json
from pathlib import Path

from receipts.usage import summarize_usage


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def test_usage_costs_use_exact_cached_token_split_and_completed_sam_image(tmp_path: Path) -> None:
    _write(
        tmp_path / "wonder_raw_000.json",
        {
            "id": "resp_wonder_1",
            "model": "muse-wonder-1.0",
            "status": "completed",
            "usage": {
                "input_tokens": 1000,
                "input_tokens_details": {"cached_tokens": 250},
                "output_tokens": 200,
                "total_tokens": 1200,
            },
        },
    )
    _write(
        tmp_path / "spark_raw_0.json",
        {
            "id": "resp_spark_1",
            "model": "muse-spark-1.3",
            "status": "completed",
            "usage": {
                "input_tokens": 100,
                "input_tokens_details": {"cached_tokens": 20},
                "output_tokens": 50,
                "total_tokens": 150,
            },
        },
    )
    _write(
        tmp_path / "sam_raw.json",
        {"response_id": "resp_sam_1", "model": "sam-3.1", "completed": True},
    )
    _write(tmp_path / "metadata.json", {"offline": False})

    usage = summarize_usage(tmp_path)
    calls = {call["provider"]: call for call in usage["calls"]}
    assert calls["wonder"]["response_id"] == "resp_wonder_1"
    assert calls["wonder"]["input_tokens"] == 1000
    assert calls["wonder"]["cached_input_tokens"] == 250
    assert calls["wonder"]["estimated_cost_usd"] == "0.00705"
    assert calls["spark"]["response_id"] == "resp_spark_1"
    assert calls["spark"]["estimated_cost_usd"] == "0.0003155"
    assert calls["sam"]["response_id"] == "resp_sam_1"
    assert calls["sam"]["estimated_cost_usd"] == "0.0025"
    assert calls["sam"]["token_status"] == "unknown"
    assert usage["totals"]["known_estimated_cost_usd"] == "0.0098655"
    assert usage["totals"]["unknown_cost_calls"] == 0
    assert usage["totals"]["by_provider"]["wonder"]["input_tokens"] == {
        "known_sum": 1000,
        "known_calls": 1,
    }
    assert usage["coverage"]["unobserved_attempts_possible"] is True
    json.dumps(usage)


def test_missing_or_aborted_usage_and_error_attempts_remain_unknown(tmp_path: Path) -> None:
    _write(
        tmp_path / "wonder_raw_000.json",
        {"id": "resp_wonder_partial", "model": "muse-wonder-1.0", "status": "incomplete"},
    )
    _write(
        tmp_path / "sam_raw.json",
        {"response_id": "resp_sam_partial", "model": "sam-3.1", "completed": False},
    )
    _write(tmp_path / "spark_error.json", {"type": "TimeoutError", "retry": False})
    _write(tmp_path / "metadata.json", {"offline": False})

    usage = summarize_usage(tmp_path)
    calls = {(call["provider"], call["artifact"]): call for call in usage["calls"]}
    assert calls[("wonder", "wonder_raw_000.json")]["cost_status"] == "unknown_usage"
    assert calls[("sam", "sam_raw.json")]["cost_status"] == "unknown_incomplete"
    assert calls[("spark", "spark_error.json")]["cost_status"] == "unknown_attempt"
    assert usage["totals"]["unknown_cost_calls"] == 3
    assert usage["totals"]["known_estimated_cost_usd"] == "0"
    assert usage["coverage"]["explicit_error_attempts"] == 1


def test_absent_cached_tokens_and_unknown_models_are_not_priced(tmp_path: Path) -> None:
    _write(
        tmp_path / "spark_raw_0.json",
        {
            "id": "resp_spark_missing_cache",
            "model": "muse-spark-1.3",
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        },
    )
    _write(
        tmp_path / "spark_raw_1.json",
        {
            "id": "resp_unknown_model",
            "model": "muse-spark-future",
            "usage": {
                "input_tokens": 10,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens": 5,
                "total_tokens": 15,
            },
        },
    )
    usage = summarize_usage(tmp_path)
    calls = {call["response_id"]: call for call in usage["calls"]}
    assert calls["resp_spark_missing_cache"]["cost_status"] == "unknown_usage"
    assert calls["resp_unknown_model"]["cost_status"] == "unknown_model"
    assert usage["coverage"]["unknown_cost_calls"] == 2


def test_sam_error_status_without_raw_response_is_an_unknown_attempt(tmp_path: Path) -> None:
    _write(tmp_path / "evidence.json", {"sam_status": "unavailable:TimeoutError"})
    _write(tmp_path / "metadata.json", {"offline": False})
    usage = summarize_usage(tmp_path)
    assert len(usage["calls"]) == 1
    call = usage["calls"][0]
    assert call["provider"] == "sam"
    assert call["artifact"] == "evidence.json"
    assert call["error_type"] == "TimeoutError"
    assert call["cost_status"] == "unknown_attempt"
    assert usage["totals"]["unknown_cost_calls"] == 1
