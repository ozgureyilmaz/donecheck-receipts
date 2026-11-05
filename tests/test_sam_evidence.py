import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

import receipts.auditor.sam_evidence as sam_evidence
from receipts.auditor.sam_evidence import _response_id, draw_overlay, parse_boxes
from receipts.config import Config


def _config() -> Config:
    return Config(
        api_key="test-only-key",
        api_base="https://api.meta.ai/v1",
        agent_model="muse-wonder-test",
        judge_model="muse-spark-test",
        sam_model="sam-test",
        mocksite_host="127.0.0.1",
        mocksite_port=8000,
        db_path=Path("unused.sqlite"),
        max_steps=10,
        api_timeout=1,
    )


def _run_fixture(tmp_path: Path) -> tuple[Path, dict, list[dict]]:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    Image.new("RGB", (64, 48), "white").save(run_dir / "frame.png")
    verdict = {"evidence_frame": 0, "evidence_concept": "payment declined message"}
    steps = [{"idx": 0, "screenshot": "frame.png", "receipt_boxes": {}}]
    return run_dir, verdict, steps


class _StubStream:
    def __init__(self, events: list, error: Exception | None = None):
        self.events = events
        self.error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return None

    def __aiter__(self):
        async def events():
            for event in self.events:
                yield event
            if self.error is not None:
                raise self.error

        return events()


class _StubResponses:
    def __init__(self, streams: list[_StubStream]):
        self.streams = streams
        self.kwargs = None
        self.calls = []

    async def create(self, **kwargs):
        self.kwargs = kwargs
        self.calls.append(kwargs)
        if not self.streams:
            raise AssertionError("Unexpected additional SAM request")
        return self.streams.pop(0)


class _StubUsage:
    def __init__(self, values: dict):
        self.values = values

    def model_dump(self, mode: str = "python") -> dict:
        return dict(self.values)


def _stub_client(monkeypatch, events: list, error: Exception | None = None) -> _StubResponses:
    responses = _StubResponses([_StubStream(events, error)])

    @asynccontextmanager
    async def fake_model_client(_config):
        yield SimpleNamespace(responses=responses)

    monkeypatch.setattr(sam_evidence, "model_client", fake_model_client)
    return responses


def _stub_client_sequence(monkeypatch, event_batches: list[list], errors: list | None = None):
    errors = errors or [None] * len(event_batches)
    responses = _StubResponses(
        [_StubStream(events, error) for events, error in zip(event_batches, errors, strict=True)]
    )

    @asynccontextmanager
    async def fake_model_client(_config):
        yield SimpleNamespace(responses=responses)

    monkeypatch.setattr(sam_evidence, "model_client", fake_model_client)
    return responses


def _declined_fixture(tmp_path: Path) -> tuple[Path, dict, list[dict], dict]:
    run_dir, verdict, _ = _run_fixture(tmp_path)
    dom_dir = run_dir / "dom"
    dom_dir.mkdir()
    (dom_dir / "toast.html").write_text(
        '<div data-receipt="error">Payment declined. Your card was not charged.</div>',
        encoding="utf-8",
    )
    (dom_dir / "summary.html").write_text(
        '<h1 data-receipt="notice">Trip summary</h1>', encoding="utf-8"
    )
    marker = {
        "x1": 10.2,
        "y1": 5.3,
        "x2": 40.0,
        "y2": 20.1,
        "w": 64,
        "h": 48,
        "visible": True,
    }
    steps = [
        {
            "idx": 21,
            "screenshot": "frame.png",
            "dom": "dom/toast.html",
            "receipt_boxes": {"error": marker},
        },
        {
            "idx": 24,
            "screenshot": "frame.png",
            "dom": "dom/summary.html",
            "receipt_boxes": {},
        },
    ]
    verdict.update({"evidence_frame": 24, "evidence_concept": "trip summary notice"})
    return run_dir, verdict, steps, marker


def _completed_events(response_id: str, output_text: str = "") -> list:
    response = SimpleNamespace(
        id=response_id,
        model="sam-test",
        usage={"input_tokens": 11, "output_tokens": 3, "total_tokens": 14},
    )
    events = []
    if output_text:
        events.append(SimpleNamespace(type="response.output_text.delta", delta=output_text))
    events.append(SimpleNamespace(type="response.completed", response=response))
    return events


def test_parse_documented_sam_box_token_sample() -> None:
    text = "<0f>0<|box;x1=211;y1=228;x2=270;y2=254;w=320;h=334|><|mask;x=0;y=0;data=27,60,~...|>"
    assert parse_boxes(text) == [{"x1": 211, "y1": 228, "x2": 270, "y2": 254, "w": 320, "h": 334}]


def test_response_ids_are_conservatively_sanitized() -> None:
    assert _response_id(" resp_abc_123 ") == "resp_abc_123"
    assert _response_id("Bearer private-token") is None
    assert _response_id("sk-private") is None
    assert _response_id(123) is None


def test_box_confidence_is_optional() -> None:
    token = "<|box;x1=1;y1=2;x2=8;y2=9;w=10;h=12;c=0.97|>"
    assert parse_boxes(token) == [{"x1": 1, "y1": 2, "x2": 8, "y2": 9, "w": 10, "h": 12}]


def test_zero_matches_is_a_valid_empty_result() -> None:
    assert parse_boxes("<0f>0<|mask;x=0;y=0;data=27,60,~...|>") == []
    assert parse_boxes("SAM found no matching region") == []


def test_invalid_geometry_is_rejected_and_multiple_boxes_are_kept() -> None:
    text = "".join(
        [
            "<|box;x1=1;y1=2;x2=8;y2=9;w=10;h=12|>",
            "<|box;x1=1;y1=2;x2=11;y2=9;w=10;h=12|>",  # x2 exceeds image width
            "<|box;x1=8;y1=2;x2=7;y2=9;w=10;h=12|>",  # reversed horizontal bounds
            "<|box;x1=0;y1=0;x2=4;y2=5;w=8;h=10|>",
        ]
    )
    assert parse_boxes(text) == [
        {"x1": 1, "y1": 2, "x2": 8, "y2": 9, "w": 10, "h": 12},
        {"x1": 0, "y1": 0, "x2": 4, "y2": 5, "w": 8, "h": 10},
    ]


def test_draw_overlay_scales_frame_coordinates(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    target = tmp_path / "overlay.png"
    Image.new("RGB", (640, 400), "white").save(source)
    box = {"x1": 20, "y1": 10, "x2": 100, "y2": 50, "w": 320, "h": 200}

    draw_overlay(source, target, [box])

    with Image.open(target) as result:
        assert result.size == (640, 400)
        assert result.getpixel((40, 20)) == (244, 63, 94)
        assert result.getpixel((200, 100)) == (244, 63, 94)
        assert result.getpixel((120, 60)) == (255, 255, 255)


def test_stream_completion_records_response_id_usage_and_boxes(tmp_path, monkeypatch) -> None:
    run_dir, verdict, steps = _run_fixture(tmp_path)
    output_text = "<0f>0<|box;x1=4;y1=5;x2=24;y2=25;w=64;h=48|>"
    response = SimpleNamespace(
        id="resp_sam_123",
        model="sam-test",
        usage=_StubUsage({"input_tokens": 31, "output_tokens": 7, "total_tokens": 38}),
    )
    stub = _stub_client(
        monkeypatch,
        [
            SimpleNamespace(type="response.output_text.delta", delta=output_text),
            SimpleNamespace(type="response.completed", response=response),
        ],
    )

    evidence = asyncio.run(sam_evidence.make_evidence(run_dir, verdict, steps, _config()))

    raw_text = (run_dir / "sam_raw.json").read_text(encoding="utf-8")
    raw = json.loads(raw_text)
    assert stub.kwargs["stream"] is True
    assert evidence["sam_status"] == "completed"
    assert evidence["source"] == "sam-test"
    assert evidence["boxes"] == [{"x1": 4, "y1": 5, "x2": 24, "y2": 25, "w": 64, "h": 48}]
    assert raw["response_id"] == "resp_sam_123"
    assert raw["usage"] == response.usage.values
    assert raw["tokens"] == 38
    assert raw["model"] == "sam-test"
    assert raw["output_text"] == output_text
    assert raw["completed"] is True
    assert "test-only-key" not in raw_text


def test_completed_zero_matches_still_record_usage(tmp_path, monkeypatch) -> None:
    run_dir, verdict, steps = _run_fixture(tmp_path)
    response = SimpleNamespace(
        id="resp_zero_456",
        model="sam-test",
        usage={"input_tokens": 29, "output_tokens": 0, "total_tokens": 29},
    )
    _stub_client(monkeypatch, [SimpleNamespace(type="response.completed", response=response)])

    evidence = asyncio.run(sam_evidence.make_evidence(run_dir, verdict, steps, _config()))

    raw = json.loads((run_dir / "sam_raw.json").read_text(encoding="utf-8"))
    assert evidence["sam_status"] == "zero_matches"
    assert evidence["source"] == "dom"
    assert raw["response_id"] == "resp_zero_456"
    assert raw["usage"]["total_tokens"] == 29
    assert raw["tokens"] == 29
    assert raw["output_text"] == ""
    assert raw["completed"] is True


@pytest.mark.parametrize(
    ("event_type", "status", "error_type"),
    [
        ("response.failed", "failed", "ProviderResponseFailed"),
        ("response.incomplete", "incomplete", "ProviderResponseIncomplete"),
    ],
)
def test_terminal_failure_preserves_partial_output_and_usage(
    tmp_path, monkeypatch, event_type, status, error_type
) -> None:
    run_dir, verdict, steps = _run_fixture(tmp_path)
    partial = "<0f>0<|box;x1=4;y1=5;x2=24;y2=25;w=64;h=48|>"
    response = SimpleNamespace(
        id="resp_failed_789",
        model="sam-test",
        status="failed",
        usage={"input_tokens": 15, "output_tokens": 3, "total_tokens": 18},
        error=SimpleNamespace(code="server_error", message="private provider detail"),
    )
    _stub_client(
        monkeypatch,
        [
            SimpleNamespace(type="response.output_text.delta", delta=partial),
            SimpleNamespace(type=event_type, response=response),
        ],
    )

    evidence = asyncio.run(sam_evidence.make_evidence(run_dir, verdict, steps, _config()))

    raw_text = (run_dir / "sam_raw.json").read_text(encoding="utf-8")
    raw = json.loads(raw_text)
    assert evidence["sam_status"] == f"unavailable:{error_type}"
    assert raw["response_id"] == "resp_failed_789"
    assert raw["usage"]["total_tokens"] == 18
    assert raw["output_text"] == partial
    assert raw["completed"] is False
    assert raw["status"] == status
    assert raw["error_type"] == error_type
    assert "private provider detail" not in raw_text


def test_stream_exception_preserves_partial_output_without_error_message(
    tmp_path, monkeypatch
) -> None:
    run_dir, verdict, steps = _run_fixture(tmp_path)
    partial = "partial SAM output"
    created_response = SimpleNamespace(
        id="resp_interrupted_101",
        model="sam-test",
        usage={"input_tokens": 12, "output_tokens": 1, "total_tokens": 13},
    )
    _stub_client(
        monkeypatch,
        [
            SimpleNamespace(type="response.created", response=created_response),
            SimpleNamespace(type="response.output_text.delta", delta=partial),
        ],
        RuntimeError("Authorization: Bearer private-token sk-private"),
    )

    evidence = asyncio.run(sam_evidence.make_evidence(run_dir, verdict, steps, _config()))

    raw_text = (run_dir / "sam_raw.json").read_text(encoding="utf-8")
    raw = json.loads(raw_text)
    assert evidence["sam_status"] == "unavailable:RuntimeError"
    assert raw["response_id"] == "resp_interrupted_101"
    assert raw["usage"]["total_tokens"] == 13
    assert raw["output_text"] == partial
    assert raw["completed"] is False
    assert raw["status"] == "stream_error"
    assert raw["error_type"] == "RuntimeError"
    assert "private-token" not in raw_text
    assert "sk-private" not in raw_text
    assert "Authorization" not in raw_text


def test_declined_toast_uses_documented_region_prompt_and_provider_box(tmp_path, monkeypatch):
    run_dir, verdict, steps, _marker = _declined_fixture(tmp_path)
    provider_box = "<0f>0<|box;x1=11;y1=5;x2=38;y2=20;w=64;h=48|>"
    stub = _stub_client_sequence(monkeypatch, [_completed_events("resp_region_123", provider_box)])

    evidence = asyncio.run(sam_evidence.make_evidence(run_dir, verdict, steps, _config()))

    prompt = stub.calls[0]["input"][0]["content"][0]["text"]
    assert prompt == "<0f>0<|box;x1=10;y1=5;x2=39;y2=20;w=64;h=48|>"
    assert evidence["frame"] == 21
    assert evidence["source"] == "sam-test"
    assert evidence["boxes"] == [{"x1": 11, "y1": 5, "x2": 38, "y2": 20, "w": 64, "h": 48}]
    assert len(evidence["attempts"]) == 1
    assert evidence["attempts"][0]["kind"] == "region"
    prompt_provenance = json.loads(
        (run_dir / "sam_prompt_00_region.json").read_text(encoding="utf-8")
    )
    raw = json.loads((run_dir / "sam_raw_00_region.json").read_text(encoding="utf-8"))
    assert prompt_provenance["frame"] == raw["prompt_provenance"]["frame"] == 21
    assert raw["prompt_provenance"]["text"] == prompt
    usage = json.loads((run_dir / "usage.json").read_text(encoding="utf-8"))
    assert [call["artifact"] for call in usage["calls"]] == ["sam_raw_00_region.json"]


def test_toast_fallbacks_are_bounded_and_each_attempt_is_accounted(tmp_path, monkeypatch):
    run_dir, verdict, steps, _marker = _declined_fixture(tmp_path)
    inadequate_text_box = "<0f>0<|box;x1=1;y1=1;x2=5;y2=4;w=64;h=48|>"
    adequate_point_box = "<0f>0<|box;x1=10;y1=5;x2=39;y2=20;w=64;h=48|>"
    stub = _stub_client_sequence(
        monkeypatch,
        [
            _completed_events("resp_region_123"),
            _completed_events("resp_text_234", inadequate_text_box),
            _completed_events("resp_point_345", adequate_point_box),
        ],
    )

    evidence = asyncio.run(sam_evidence.make_evidence(run_dir, verdict, steps, _config()))

    prompts = [call["input"][0]["content"][0]["text"] for call in stub.calls]
    assert prompts == [
        "<0f>0<|box;x1=10;y1=5;x2=39;y2=20;w=64;h=48|>",
        "trip summary notice",
        "<0f>0<|point;x=24;y=12;w=64;h=48|>-<|point;x=24;y=4;w=64;h=48|>",
    ]
    assert [attempt["kind"] for attempt in evidence["attempts"]] == [
        "region",
        "text",
        "point",
    ]
    assert evidence["source"] == "sam-test"
    assert evidence["boxes"] == [{"x1": 10, "y1": 5, "x2": 39, "y2": 20, "w": 64, "h": 48}]
    usage = json.loads((run_dir / "usage.json").read_text(encoding="utf-8"))
    assert [call["artifact"] for call in usage["calls"]] == [
        "sam_raw_00_region.json",
        "sam_raw_01_text.json",
        "sam_raw_02_point.json",
    ]
    assert all(call["total_tokens"] == 14 for call in usage["calls"])


def test_toast_provider_error_stops_without_fallback_requests(tmp_path, monkeypatch):
    run_dir, verdict, steps, marker = _declined_fixture(tmp_path)
    failed_response = SimpleNamespace(
        id="resp_region_failed",
        model="sam-test",
        usage={"input_tokens": 4, "output_tokens": 1, "total_tokens": 5},
    )
    stub = _stub_client_sequence(
        monkeypatch,
        [[SimpleNamespace(type="response.failed", response=failed_response)]],
    )

    evidence = asyncio.run(sam_evidence.make_evidence(run_dir, verdict, steps, _config()))

    assert len(stub.calls) == 1
    assert evidence["sam_status"] == "unavailable:ProviderResponseFailed"
    assert evidence["source"] == "dom"
    assert evidence["boxes"] == [marker]
    usage = json.loads((run_dir / "usage.json").read_text(encoding="utf-8"))
    assert [call["artifact"] for call in usage["calls"]] == ["sam_raw_00_region.json"]


def test_toast_prefers_verdict_frame_when_it_has_visible_decline_marker(tmp_path, monkeypatch):
    run_dir, verdict, steps, marker = _declined_fixture(tmp_path)
    summary_path = run_dir / "dom" / "summary.html"
    summary_path.write_text(
        '<div data-receipt="error">Payment declined. Your card was not charged.</div>',
        encoding="utf-8",
    )
    steps[1]["receipt_boxes"]["error"] = {**marker, "x1": 20, "x2": 50}
    stub = _stub_client_sequence(
        monkeypatch,
        [_completed_events("resp_preferred_456", "<0f>0<|box;x1=20;y1=5;x2=49;y2=20;w=64;h=48|>")],
    )

    evidence = asyncio.run(sam_evidence.make_evidence(run_dir, verdict, steps, _config()))

    assert stub.calls[0]["input"][0]["content"][0]["text"] == (
        "<0f>0<|box;x1=20;y1=5;x2=49;y2=20;w=64;h=48|>"
    )
    assert evidence["frame"] == 24
