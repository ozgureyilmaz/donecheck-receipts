import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from receipts.agent.wonder_driver import AgentStopped, allowed_url, run_wonder
from receipts.config import load_config


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
                    "pending_safety_checks": [{"id": "s1", "code": "review", "message": "Review"}],
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
        db=SimpleNamespace(event=lambda *args: events.append(args)),
    )
    with pytest.raises(AgentStopped, match="Pending safety"):
        asyncio.run(run_wonder(None, recorder, {}, replace(load_config(), api_key="fake-key"), []))
    assert len(requests) == 1  # Only initial request; no computer_call_output.
    assert events[0][1] == "pending_safety_checks"


def _fake_wonder_run(tmp_path, monkeypatch, call_items, max_steps):
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
    config = replace(load_config(), api_key="fake-key", max_steps=max_steps)
    return Page(), recorder, config, requests, executed_actions, recorded_actions


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
