"""SAM 3.1 box tokens and accurately labeled DOM fallback overlays."""

import re
from pathlib import Path

from PIL import Image, ImageDraw

from receipts.common import image_url, write_json
from receipts.config import Config, model_client

BOX_RE = re.compile(
    r"<\|box;x1=(\d+);y1=(\d+);x2=(\d+);y2=(\d+);w=(\d+);h=(\d+)(?:;c=(?:\d+(?:\.\d+)?))?\|>"
)


def parse_boxes(text: str) -> list[dict]:
    boxes = []
    for match in BOX_RE.finditer(text):
        box = dict(zip(("x1", "y1", "x2", "y2", "w", "h"), map(int, match.groups()), strict=True))
        if 0 <= box["x1"] < box["x2"] <= box["w"] and 0 <= box["y1"] < box["y2"] <= box["h"]:
            boxes.append(box)
    return boxes


def draw_overlay(source: Path, target: Path, boxes: list[dict]) -> None:
    with Image.open(source) as original:
        image = original.convert("RGB")
        draw = ImageDraw.Draw(image)
        for box in boxes:
            scale_x, scale_y = image.width / box["w"], image.height / box["h"]
            rect = (
                box["x1"] * scale_x,
                box["y1"] * scale_y,
                box["x2"] * scale_x,
                box["y2"] * scale_y,
            )
            draw.rectangle(rect, outline="#f43f5e", width=5)
        image.save(target)


async def make_evidence(
    run_dir: Path, verdict: dict, steps: list[dict], config: Config, offline: bool = False
) -> dict:
    frame = next(
        (step for step in steps if step["idx"] == verdict.get("evidence_frame")),
        steps[-1] if steps else None,
    )
    evidence = {
        "frame": frame["idx"] if frame else None,
        "concept": verdict.get("evidence_concept", "booking details"),
        "boxes": [],
        "mask_svg": None,
        "source": "dom",
        "sam_status": "not_called",
        "overlay": None,
    }
    if frame is None:
        return evidence
    boxes = []
    if not offline and config.api_key:
        try:
            async with model_client(config) as client:
                # SAM's output is a streamed special-token lane, per reading-segmentation docs.
                stream = await client.responses.create(
                    model=config.sam_model,
                    stream=True,
                    input=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": evidence["concept"] or "booking details",
                                },
                                {
                                    "type": "input_image",
                                    "image_url": image_url(run_dir / frame["screenshot"]),
                                },
                            ],
                        }
                    ],
                )
                chunks, completed = [], False
                async with stream:
                    async for event in stream:
                        if event.type == "response.output_text.delta":
                            chunks.append(event.delta)
                        elif event.type == "response.completed":
                            completed = True
                        elif event.type in {"error", "response.failed", "response.incomplete"}:
                            raise ValueError("SAM stream incomplete")
                raw = "".join(chunks)
                write_json(
                    run_dir / "sam_raw.json",
                    {"output_text": raw, "completed": completed, "model": config.sam_model},
                )
                if not completed:
                    raise ValueError("SAM stream did not complete")
                boxes = parse_boxes(raw)
                evidence["sam_status"] = "completed" if boxes else "zero_matches"
                if boxes:
                    evidence["source"] = config.sam_model
        except Exception as exc:
            evidence["sam_status"] = f"unavailable:{type(exc).__name__}"
    if not boxes:
        markers = frame.get("receipt_boxes", {})
        keys = ("error", "pending", "pnr", "date", "last")
        candidate = next((markers[key] for key in keys if markers.get(key)), None)
        if candidate:
            boxes = [candidate]
        evidence["source"] = "dom"
    evidence["boxes"] = boxes
    target = run_dir / "evidence_overlay.png"
    draw_overlay(run_dir / frame["screenshot"], target, boxes)
    evidence["overlay"] = target.name
    return evidence
