import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from receipts.auditor.spark_judge import judge, validate_judgment
from receipts.config import load_config


def judgment(**overrides):
    return {
        "verdict": "FALSE_DONE",
        "confidence": 0.9,
        "evidence_frame": 7,
        "evidence_t_s": 99,
        "evidence_concept": "payment declined message",
        "rationale": "Visible payment failure",
        **overrides,
    }


def test_judge_timestamp_comes_from_recorded_frame():
    assert validate_judgment(judgment(), [{"idx": 7, "t_s": 2.4}])["evidence_t_s"] == 2.4


@pytest.mark.parametrize(
    "overrides",
    [
        {"evidence_frame": 999},
        {"evidence_frame": None},
        {"confidence": float("nan")},
        {"verdict": "DONE"},
        {"rationale": []},
    ],
)
def test_bad_judgment_rejected(overrides):
    with pytest.raises(ValueError):
        validate_judgment(judgment(**overrides), [{"idx": 7, "t_s": 2.4}])


def test_offline_judge_never_constructs_a_client(monkeypatch, tmp_path):
    def forbidden(*args):
        raise AssertionError("A client was constructed offline")

    monkeypatch.setattr("receipts.auditor.spark_judge.model_client", forbidden)
    result = asyncio.run(judge(tmp_path, {}, "Flight booked", [], load_config(), offline=True))
    assert result["verdict"] == "UNSURE"
    assert result["available"] is False


def test_network_failure_not_retried(monkeypatch, tmp_path):
    from PIL import Image

    Image.new("RGB", (2, 2)).save(tmp_path / "frame.png")
    calls = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        @property
        def responses(self):
            return SimpleNamespace(create=self.create)

        async def create(self, **kwargs):
            calls.append(kwargs)
            raise ConnectionError("ambiguous provider request")

    monkeypatch.setattr("receipts.auditor.spark_judge.model_client", lambda _: FakeClient())
    config = replace(load_config(), api_key="test-fake")
    result = asyncio.run(
        judge(
            tmp_path,
            {},
            "Flight booked",
            [{"idx": 7, "t_s": 2.4, "screenshot": "frame.png"}],
            config,
        )
    )
    assert result["verdict"] == "UNSURE"
    assert len(calls) == 1
