import asyncio
import json
from dataclasses import replace
from importlib.metadata import version
from types import SimpleNamespace

import httpx
import pytest
from openai import AsyncOpenAI, BadRequestError

from receipts.agent.wonder_driver import AgentStopped, allowed_url, run_wonder
from receipts.config import Config

MOCK_CHECK = {"id": "s1", "code": "computer_action_requires_confirmation", "message": "Review"}


def _test_config(tmp_path, max_steps=40):
    return Config(
        api_key="fake-key",
        api_base="https://api.meta.ai/v1",
        agent_model="muse-wonder-1.0",
        judge_model="muse-spark-1.3",
        sam_model="sam-3.1",
        mocksite_host="127.0.0.1",
        mocksite_port=8000,
        db_path=tmp_path / "audit.sqlite",
        max_steps=max_steps,
        api_timeout=5.0,
    )


@pytest.mark.parametrize(
    "url,expected",
    [
        ("http://127.0.0.1:8000/payment", True),
        ("https://127.0.0.1:8000", False),
        ("http://127.0.0.1:8001", False),
        ("http://localhost:8000", False),
        ("http://127.0.0.1:8000.evil.test", False),
        ("http://127.0.0.1:8000/api/state?session=a", False),
        ("http://127.0.0.1:8000/%61pi/state", False),
        ("http://127.0.0.1:8000/results/../api/state", False),
        ("http://127.0.0.1:8000/api", False),
        ("http://user@127.0.0.1:8000/", False),
        ("file:///etc/passwd", False),
    ],
)
def test_navigation_boundary(url, expected):
    assert allowed_url(url, 8000) is expected


def test_safety_checks_stop_without_actions_or_acknowledgment(tmp_path, monkeypatch):
    from PIL import Image

    Image.new("RGB", (2, 2)).save(tmp_path / "frame.png")
    events, requests = [], []
    response = SimpleNamespace(
        model_dump=lambda **kwargs: {
            "status": "completed",
            "output": [
                {
                    "type": "computer_call",
                    "call_id": "c1",
                    "actions": [{"type": "click", "x": 1, "y": 1}],
                    "pending_safety_checks": [MOCK_CHECK],
                }
            ],
        }
    )

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        @property
        def responses(self):
            return SimpleNamespace(create=self.create)

        async def create(self, **kwargs):
            requests.append(kwargs)
            return response

    monkeypatch.setattr("receipts.agent.wonder_driver.model_client", lambda _: Client())
    recorder = SimpleNamespace(
        steps=[{"screenshot": "frame.png"}],
        run_dir=tmp_path,
        run_id="r",
        db=SimpleNamespace(safety_check=lambda _, payload: events.append(payload)),
    )
    page = SimpleNamespace(url="http://127.0.0.1:8000/")
    with pytest.raises(AgentStopped, match="mock_confirmation_flag_required"):
        asyncio.run(run_wonder(page, recorder, {}, _test_config(tmp_path), []))
    assert len(requests) == 1  # Only initial request; no computer_call_output.
    assert events[0]["decision"] == "declined"
    assert events[0]["id"] == MOCK_CHECK["id"]


def _fake_wonder_run(tmp_path, monkeypatch, call_items, max_steps, *, terminal_claim=None):
    from PIL import Image

    Image.new("RGB", (2, 2)).save(tmp_path / "frame.png")
    requests, executed_actions, recorded_actions = [], [], []
    response = SimpleNamespace(
        id="response-1",
        output_text="",
        model_dump=lambda **kwargs: {"status": "completed", "output": call_items},
    )

    class Keyboard:
        async def insert_text(self, value):
            executed_actions.append(value)

    class Page:
        url = "http://127.0.0.1:8000/"
        keyboard = Keyboard()

        async def wait_for_timeout(self, _milliseconds):
            return None

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        @property
        def responses(self):
            return SimpleNamespace(create=self.create)

        async def create(self, **kwargs):
            requests.append(kwargs)
            if len(requests) > 1 and terminal_claim is not None:
                return SimpleNamespace(
                    output_text=terminal_claim,
                    model_dump=lambda **_: {"status": "completed", "output": []},
                )
            return response

    async def capture(action):
        recorded_actions.append(action)
        return {"screenshot": "frame.png"}

    recorder = SimpleNamespace(
        steps=[{"screenshot": "frame.png"}],
        run_dir=tmp_path,
        capture=capture,
        db=SimpleNamespace(event=lambda *args: None),
        run_id="r",
    )
    monkeypatch.setattr("receipts.agent.wonder_driver.model_client", lambda _: Client())
    config = _test_config(tmp_path, max_steps)
    return Page(), recorder, config, requests, executed_actions, recorded_actions


def test_mock_confirmation_is_logged_before_action_and_echoed_unchanged(tmp_path, monkeypatch):
    check = {**MOCK_CHECK, "provider_field": {"keep": "unchanged"}}
    call = {
        "type": "computer_call",
        "call_id": "c1",
        "actions": [{"type": "type", "text": "one"}],
        "pending_safety_checks": [check],
    }
    page, recorder, config, requests, executed, _ = _fake_wonder_run(
        tmp_path, monkeypatch, [call], max_steps=2, terminal_claim="finished"
    )
    events = []
    original_type = page.keyboard.insert_text

    async def type_after_audit(text):
        assert events[0]["decision"] == "approved"
        await original_type(text)

    page.keyboard.insert_text = type_after_audit
    recorder.db.safety_check = lambda _, payload: events.append(payload)
    result = asyncio.run(
        run_wonder(page, recorder, {}, replace(config, allow_mock_confirmations=True), [])
    )
    assert result == "finished" and executed == ["one"]
    assert events[0]["step"] == 1 and events[0]["url"] == page.url
    followup = requests[1]
    assert followup["previous_response_id"] == "response-1"
    assert followup["input"][0]["call_id"] == "c1"
    assert followup["input"][0]["acknowledged_safety_checks"] == [check]


@pytest.mark.parametrize(
    "checks,max_steps,expected_rules",
    [
        (
            [MOCK_CHECK, {**MOCK_CHECK, "id": "s2", "code": "malicious_instructions"}],
            2,
            ["batch_contains_declined_check", "code_not_allowlisted"],
        ),
        ([MOCK_CHECK], 1, ["confirmation_exceeds_step_budget"]),
        (MOCK_CHECK, 2, ["invalid_safety_checks_array"]),
    ],
)
def test_declined_batch_executes_no_actions(
    tmp_path, monkeypatch, checks, max_steps, expected_rules
):
    call = {
        "type": "computer_call",
        "call_id": "c1",
        "actions": [{"type": "type", "text": "one"}],
        "pending_safety_checks": checks,
    }
    page, recorder, config, requests, executed, _ = _fake_wonder_run(
        tmp_path, monkeypatch, [call], max_steps=max_steps
    )
    events = []
    recorder.db.safety_check = lambda _, payload: events.append(payload)
    with pytest.raises(AgentStopped, match="Safety check declined"):
        asyncio.run(
            run_wonder(page, recorder, {}, replace(config, allow_mock_confirmations=True), [])
        )
    assert not executed and len(requests) == 1
    assert [event["rule"] for event in events] == expected_rules
    assert all(event["decision"] == "declined" for event in events)


def test_action_budget_stops_before_excess_action_without_followup_call(tmp_path, monkeypatch):
    actions = [
        {"type": "type", "text": "first"},
        {"type": "type", "text": "second"},
    ]
    call = {"type": "computer_call", "call_id": "c1", "actions": actions}
    page, recorder, config, requests, executed, recorded = _fake_wonder_run(
        tmp_path, monkeypatch, [call], max_steps=1
    )

    with pytest.raises(AgentStopped, match="Action step budget reached"):
        asyncio.run(run_wonder(page, recorder, {}, config, []))

    assert executed == ["first"]
    assert [action["text"] for action in recorded] == ["first"]
    assert len(requests) == 1


def test_response_budget_allows_one_call_and_makes_no_second_request(tmp_path, monkeypatch):
    call = {
        "type": "computer_call",
        "call_id": "c1",
        "actions": [{"type": "type", "text": "one"}],
    }
    page, recorder, config, requests, executed, recorded = _fake_wonder_run(
        tmp_path, monkeypatch, [call], max_steps=1
    )

    with pytest.raises(AgentStopped, match="Step budget reached"):
        asyncio.run(run_wonder(page, recorder, {}, config, []))

    assert executed == ["one"]
    assert len(requests) == 1
    assert recorded[-1] == {"type": "computer_call_output", "call_id": "c1"}


def test_multiple_computer_calls_in_one_response_are_rejected_before_actions(tmp_path, monkeypatch):
    first = {"type": "computer_call", "call_id": "c1", "actions": [{"type": "type", "text": "one"}]}
    second = {
        "type": "computer_call",
        "call_id": "c2",
        "actions": [{"type": "type", "text": "two"}],
    }
    page, recorder, config, requests, executed, recorded = _fake_wonder_run(
        tmp_path, monkeypatch, [first, second], max_steps=2
    )

    with pytest.raises(AgentStopped, match="at most one computer call"):
        asyncio.run(run_wonder(page, recorder, {}, config, []))

    assert executed == []
    assert recorded == []
    assert len(requests) == 1


def test_documented_request_is_sent_once_and_rejection_is_preserved(tmp_path, monkeypatch):
    (tmp_path / "frame.png").write_bytes(b"fixture screenshot")
    requests = []

    def reject(request):
        body = json.loads(request.content)
        requests.append(
            {
                "method": request.method,
                "url": str(request.url),
                "model": body["model"],
                "tools": body["tools"],
            }
        )
        return httpx.Response(
            400,
            headers={"x-request-id": "fixture-rejected"},
            json={"error": {"message": "Unsupported tool type: 'computer'", "param": "tools"}},
        )

    def client(config):
        return AsyncOpenAI(
            base_url=config.api_base,
            api_key=config.api_key,
            max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(reject)),
        )

    monkeypatch.setattr("receipts.agent.wonder_driver.model_client", client)
    recorder = SimpleNamespace(steps=[{"screenshot": "frame.png"}], run_dir=tmp_path)
    with pytest.raises(BadRequestError):
        asyncio.run(run_wonder(None, recorder, {}, _test_config(tmp_path), []))

    assert requests == [
        {
            "method": "POST",
            "url": "https://api.meta.ai/v1/responses",
            "model": "muse-wonder-1.0",
            "tools": [{"type": "computer"}],
        }
    ]
    print(json.dumps({"openai_sdk": version("openai"), **requests[0]}))
    assert json.loads((tmp_path / "wonder_error.json").read_text()) == {
        "type": "BadRequestError",
        "model": "muse-wonder-1.0",
        "request_id": "fixture-rejected",
        "status_code": 400,
    }
