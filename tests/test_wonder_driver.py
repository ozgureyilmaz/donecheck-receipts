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
