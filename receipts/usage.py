"""Offline token usage and estimated model cost from stored response artifacts.

This module never loads environment files or makes provider calls. It accounts
only for usage explicitly present in the run's redacted response/error files.
"""

from __future__ import annotations

import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from receipts.common import write_json

_MILLION = Decimal("1000000")
_TOKEN_RATES_PER_MILLION: dict[str, dict[str, Decimal]] = {
    # Rates are input / cached input / output USD per million tokens.
    "muse-wonder-1.0": {
        "input": Decimal("4"),
        "cached_input": Decimal("0.20"),
        "output": Decimal("20"),
    },
    "muse-spark-1.3": {
        "input": Decimal("1.25"),
        "cached_input": Decimal("0.15"),
        "output": Decimal("4.25"),
    },
}
_SAM_IMAGE_USD = Decimal("0.0025")
_SAM_PRICING_URL = "https://dev.meta.ai/products/meta-model-api"
_ERROR_ARTIFACTS = {
    "wonder": ("wonder_error.json",),
    "spark": ("spark_error.json",),
    "sam": ("sam_error.json",),
}


def record_model_result(run_dir: Path, artifact: str, payload: dict[str, Any]) -> None:
    """Preserve the response and refresh usage before the next model request."""
    write_json(run_dir / artifact, payload)
    write_json(run_dir / "usage.json", summarize_usage(run_dir))


def _read_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, type(exc).__name__
    if not isinstance(value, dict):
        return None, "not_an_object"
    return value, None


def _count(value: Any) -> int | None:
    if type(value) is int and value >= 0:
        return value
    return None


def _decimal_text(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _usage_fields(raw: dict[str, Any]) -> dict[str, int | None]:
    usage = raw.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    details = usage.get("input_tokens_details")
    if not isinstance(details, dict):
        details = {}
    return {
        "input_tokens": _count(usage.get("input_tokens")),
        "cached_input_tokens": _count(details.get("cached_tokens")),
        "output_tokens": _count(usage.get("output_tokens")),
        "total_tokens": _count(usage.get("total_tokens")),
    }


def _token_status(tokens: dict[str, int | None]) -> str:
    if all(tokens[key] is not None for key in tokens):
        return "complete"
    if any(value is not None for value in tokens.values()):
        return "partial"
    return "unknown"


def _call_base(provider: str, artifact: str) -> dict[str, Any]:
    return {
        "provider": provider,
        "artifact": artifact,
        "response_id": None,
        "request_id": None,
        "model": None,
        "status": "unknown",
        "completed": None,
        "input_tokens": None,
        "cached_input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
        "token_status": "unknown",
        "estimated_cost_usd": None,
        "cost_status": "unknown",
        "pricing_basis": None,
    }


def _response_ids(call: dict[str, Any], raw: dict[str, Any]) -> None:
    response_id = raw.get("response_id") or raw.get("id")
    request_id = raw.get("request_id")
    call["response_id"] = response_id if isinstance(response_id, str) else None
    call["request_id"] = request_id if isinstance(request_id, str) else None
    model = raw.get("model")
    call["model"] = model if isinstance(model, str) else None
    status = raw.get("status")
    if isinstance(status, str):
        call["status"] = status
    completed = raw.get("completed")
    if isinstance(completed, bool):
        call["completed"] = completed
    elif status == "completed":
        call["completed"] = True
    elif status in {"failed", "incomplete", "error"}:
        call["completed"] = False
    call.update(_usage_fields(raw))
    call["token_status"] = _token_status(
        {
            key: call[key]
            for key in ("input_tokens", "cached_input_tokens", "output_tokens", "total_tokens")
        }
    )


def _token_cost(call: dict[str, Any]) -> tuple[Decimal | None, str, str | None]:
    model = call["model"]
    rates = _TOKEN_RATES_PER_MILLION.get(model)
    if rates is None:
        return None, "unknown_model", None
    input_tokens = call["input_tokens"]
    cached_tokens = call["cached_input_tokens"]
    output_tokens = call["output_tokens"]
    if input_tokens is None or cached_tokens is None or output_tokens is None:
        return None, "unknown_usage", None
    if cached_tokens > input_tokens:
        return None, "invalid_usage", None
    uncached_tokens = input_tokens - cached_tokens
    cost = (
        Decimal(uncached_tokens) * rates["input"]
        + Decimal(cached_tokens) * rates["cached_input"]
        + Decimal(output_tokens) * rates["output"]
    ) / _MILLION
    return (
        cost,
        "estimated",
        "input/cached-input/output tokens at USD per million; cached tokens are inside input_tokens",
    )


def _response_call(provider: str, path: Path) -> dict[str, Any]:
    call = _call_base(provider, path.name)
    raw, error = _read_json(path)
    if error is not None or raw is None:
        call.update(
            {
                "status": "invalid_response_artifact",
                "artifact_parse_error": error,
                "cost_status": "unknown_usage",
            }
        )
        return call
    _response_ids(call, raw)
    if provider in {"wonder", "spark"}:
        cost, status, basis = _token_cost(call)
        call["cost_status"] = status
        call["pricing_basis"] = basis
        if cost is not None:
            call["estimated_cost_usd"] = _decimal_text(cost)
        return call

    # Meta's SAM rate is per successful image, independent of token usage.
    if call["completed"] is True:
        call.update(
            {
                "image_count": 1,
                "estimated_cost_usd": _decimal_text(_SAM_IMAGE_USD),
                "cost_status": "estimated",
                "pricing_basis": "one completed image at USD 2.50 per 1,000 images",
            }
        )
    else:
        call.update(
            {
                "image_count": None,
                "cost_status": "unknown_incomplete"
                if call["completed"] is False
                else "unknown_status",
                "pricing_basis": None,
            }
        )
    return call


def _error_call(provider: str, path: Path) -> dict[str, Any]:
    call = _call_base(provider, path.name)
    raw, error = _read_json(path)
    call["status"] = "error_artifact"
    call["completed"] = False
    call["cost_status"] = "unknown_attempt"
    call["artifact_parse_error"] = error
    if raw is not None:
        response_id = raw.get("response_id") or raw.get("id")
        request_id = raw.get("request_id")
        model = raw.get("model")
        call["response_id"] = response_id if isinstance(response_id, str) else None
        call["request_id"] = request_id if isinstance(request_id, str) else None
        call["model"] = model if isinstance(model, str) else None
        tokens = _usage_fields(raw)
        call.update(tokens)
        call["token_status"] = _token_status(tokens)
        error_type = raw.get("error_type") or raw.get("type")
        call["error_type"] = error_type if isinstance(error_type, str) else None
    return call


def _sum_known_tokens(calls: list[dict[str, Any]], key: str) -> dict[str, int]:
    values = [call[key] for call in calls if isinstance(call.get(key), int)]
    return {"known_sum": sum(values), "known_calls": len(values)}


def _group_summary(calls: list[dict[str, Any]]) -> dict[str, Any]:
    known_costs = [
        Decimal(call["estimated_cost_usd"])
        for call in calls
        if isinstance(call.get("estimated_cost_usd"), str)
    ]
    return {
        "observed_calls": len(calls),
        "token_complete_calls": sum(call["token_status"] == "complete" for call in calls),
        "token_partial_calls": sum(call["token_status"] == "partial" for call in calls),
        "token_unknown_calls": sum(call["token_status"] == "unknown" for call in calls),
        "input_tokens": _sum_known_tokens(calls, "input_tokens"),
        "cached_input_tokens": _sum_known_tokens(calls, "cached_input_tokens"),
        "output_tokens": _sum_known_tokens(calls, "output_tokens"),
        "total_tokens": _sum_known_tokens(calls, "total_tokens"),
        "known_estimated_cost_usd": _decimal_text(sum(known_costs, Decimal(0))),
        "unknown_cost_calls": sum(call.get("estimated_cost_usd") is None for call in calls),
    }


def _group(calls: list[dict[str, Any]], key: str) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for call in calls:
        value = call.get(key)
        grouped[value if isinstance(value, str) and value else "unknown"].append(call)
    return {name: _group_summary(values) for name, values in sorted(grouped.items())}


def _load_evidence_status(run_dir: Path) -> str | None:
    path = run_dir / "evidence.json"
    raw, error = _read_json(path)
    if error is not None or raw is None:
        return None
    status = raw.get("sam_status")
    return status if isinstance(status, str) else None


def summarize_usage(run_dir: Path) -> dict[str, Any]:
    """Summarize stored Wonder, Spark, and SAM usage without calling a model.

    Token estimates use only explicit integer usage counters. Cached input is a
    subset of ``input_tokens`` and is priced separately without being added a
    second time. SAM cost is estimated only for a response explicitly marked
    completed. All money values are decimal strings so the returned object is
    safe for ordinary JSON serialization.
    """
    root = Path(run_dir)
    calls: list[dict[str, Any]] = []
    response_paths: list[tuple[str, Path]] = []
    response_paths.extend(("wonder", path) for path in sorted(root.glob("wonder_raw_*.json")))
    response_paths.extend(("spark", path) for path in sorted(root.glob("spark_raw_*.json")))
    sam_path = root / "sam_raw.json"
    if sam_path.is_file():
        response_paths.append(("sam", sam_path))
    for provider, path in response_paths:
        calls.append(_response_call(provider, path))

    error_paths: list[tuple[str, Path]] = []
    for provider, names in _ERROR_ARTIFACTS.items():
        error_paths.extend((provider, root / name) for name in names if (root / name).is_file())
    for provider, path in error_paths:
        calls.append(_error_call(provider, path))

    evidence_status = _load_evidence_status(root)
    if (
        evidence_status
        and evidence_status.startswith("unavailable:")
        and not any(call["provider"] == "sam" for call in calls)
    ):
        inferred = _call_base("sam", "evidence.json")
        inferred.update(
            {
                "status": "sam_error_status",
                "completed": False,
                "error_type": evidence_status.partition(":")[2] or None,
                "cost_status": "unknown_attempt",
            }
        )
        calls.append(inferred)

    calls.sort(key=lambda call: (call["provider"], call["artifact"]))
    total_known_cost = sum(
        (
            Decimal(call["estimated_cost_usd"])
            for call in calls
            if isinstance(call.get("estimated_cost_usd"), str)
        ),
        Decimal(0),
    )
    metadata, _ = _read_json(root / "metadata.json")
    offline = metadata.get("offline") is True if metadata is not None else None
    unobserved_attempts_possible = offline is not True
    caveat = (
        "Only raw response and explicit error/status artifacts are counted. "
        "An attempted request with no such artifact cannot be quantified."
        if unobserved_attempts_possible
        else "Run metadata marks this episode offline; provider calls were not configured."
    )
    token_counts = {
        provider: sum(call["provider"] == provider for call in calls)
        for provider in ("wonder", "spark", "sam")
    }
    raw_response_count = len(response_paths)
    explicit_error_count = len(error_paths) + sum(
        call["artifact"] == "evidence.json" for call in calls
    )
    return {
        "schema_version": 1,
        "run_id": root.name,
        "calls": calls,
        "totals": {
            "observed_calls": len(calls),
            "response_artifacts": raw_response_count,
            "explicit_error_attempts": explicit_error_count,
            "by_provider": _group(calls, "provider"),
            "by_model": _group(calls, "model"),
            "known_estimated_cost_usd": _decimal_text(total_known_cost),
            "unknown_cost_calls": sum(call.get("estimated_cost_usd") is None for call in calls),
        },
        "coverage": {
            "provider_calls_configured_offline": offline,
            "response_artifacts": raw_response_count,
            "explicit_error_attempts": explicit_error_count,
            "calls_by_provider": token_counts,
            "token_complete_calls": sum(call["token_status"] == "complete" for call in calls),
            "token_partial_calls": sum(call["token_status"] == "partial" for call in calls),
            "token_unknown_calls": sum(call["token_status"] == "unknown" for call in calls),
            "estimated_cost_calls": sum(
                call.get("estimated_cost_usd") is not None for call in calls
            ),
            "unknown_cost_calls": sum(call.get("estimated_cost_usd") is None for call in calls),
            "unobserved_attempts_possible": unobserved_attempts_possible,
            "caveat": caveat,
        },
        "pricing": {
            "rates_per_million_tokens_usd": {
                model: {name: _decimal_text(rate) for name, rate in rates.items()}
                for model, rates in _TOKEN_RATES_PER_MILLION.items()
            },
            "sam_completed_image_usd": _decimal_text(_SAM_IMAGE_USD),
            "sam_pricing_source": _SAM_PRICING_URL,
        },
    }
