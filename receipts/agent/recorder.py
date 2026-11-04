"""Per-action recording plus a watcher for transient UI changes."""

import asyncio
import hashlib
import json
import time
from pathlib import Path

from playwright.async_api import Page

from receipts.audit.db import AuditDB, redact
from receipts.common import utc_now


class Recorder:
    def __init__(self, page: Page, run_dir: Path, db: AuditDB, run_id: str, started: float):
        self.page, self.run_dir, self.db, self.run_id = page, run_dir, db, run_id
        self.started = started
        self.steps: list[dict] = []
        self.lock = asyncio.Lock()
        self.last_dom_hash = ""
        self.stopping = False
        self.error: str | None = None
        (run_dir / "screens").mkdir()
        (run_dir / "dom").mkdir()
        (run_dir / "steps.jsonl").touch()

    async def capture(self, action: dict, changed_only: bool = False) -> dict | None:
        async with self.lock:
            idx = len(self.steps)
            screen = f"screens/{idx:03d}.png"
            dom = f"dom/{idx:03d}.html"
            for attempt in range(3):
                html = await self.page.content()
                digest = hashlib.sha256(html.encode()).hexdigest()
                if changed_only and digest == self.last_dom_hash:
                    return None
                await self.page.screenshot(path=str(self.run_dir / screen), animations="disabled")
                t_s, ts = round(time.monotonic() - self.started, 4), utc_now()
                # Record only visible element geometry, in screenshot pixel coordinates.
                boxes = await self.page.locator("[data-receipt]").evaluate_all("""els => Object.fromEntries(els.map(el => {
                    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
                    return [el.dataset.receipt, {x1:Math.max(0,r.left),y1:Math.max(0,r.top),x2:Math.min(innerWidth,r.right),y2:Math.min(innerHeight,r.bottom),w:innerWidth,h:innerHeight,visible:r.width>0&&r.height>0&&r.bottom>0&&r.right>0&&r.top<innerHeight&&r.left<innerWidth&&s.visibility!=='hidden'&&s.display!=='none'}];
                }).filter(([,v])=>v.visible))""")
                if html == await self.page.content():
                    break
                if attempt == 2:
                    raise RuntimeError(
                        "DOM changed during screenshot; refusing mismatched evidence"
                    )
            (self.run_dir / dom).write_text(redact(html))
            step = {
                "idx": idx,
                "t_s": t_s,
                "ts": ts,
                "action": redact(action),
                "url": self.page.url,
                "screenshot": screen,
                "dom": dom,
                "receipt_boxes": boxes,
            }
            self.db.add_step(self.run_id, step, self.run_dir)
            with (self.run_dir / "steps.jsonl").open("a") as log:
                log.write(json.dumps(step) + "\n")
            self.steps.append(step)
            self.last_dom_hash = digest
            return step

    async def watch(self) -> None:
        while not self.stopping:
            await asyncio.sleep(0.35)
            try:
                await self.capture({"type": "ui_change"}, changed_only=True)
            except Exception as exc:
                if (
                    not self.stopping
                    and "navigat" not in str(exc).lower()
                    and not self.page.is_closed()
                ):
                    self.error = type(exc).__name__

    async def stop(self, task: asyncio.Task) -> None:
        self.stopping = True
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
