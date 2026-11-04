import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

from receipts.agent.recorder import Recorder


def test_recorder_does_not_pair_disappearing_toast_dom_with_summary_screenshot(tmp_path):
    class Page:
        html = '<p data-receipt="error">Payment declined</p>'
        url = "http://127.0.0.1:8000/summary"
        screenshots = 0

        async def content(self):
            return self.html

        async def screenshot(self, *, path, animations):
            self.screenshots += 1
            # A UI timer fires between the initial DOM read and first screenshot.
            self.html = "<h1>Trip summary</h1>"
            Path(path).write_text(self.html)

        def locator(self, selector):
            async def boxes(script):
                return {}

            return SimpleNamespace(evaluate_all=boxes)

    page = Page()
    db = SimpleNamespace(add_step=lambda *args: None)
    recorder = Recorder(page, tmp_path, db, "run-1", time.monotonic())
    step = asyncio.run(recorder.capture({"type": "wait"}))
    assert page.screenshots == 2
    assert (tmp_path / step["dom"]).read_text() == (tmp_path / step["screenshot"]).read_text()
    assert "Payment declined" not in (tmp_path / step["dom"]).read_text()
