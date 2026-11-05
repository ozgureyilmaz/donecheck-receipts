"""Send one minimal, documented computer tool request and retain redacted evidence."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

from PIL import Image, ImageDraw

from receipts.audit.db import redact
from receipts.common import image_url, write_json
from receipts.config import Config, load_config, model_client
from receipts.usage import record_model_result


async def probe(config: Config, model: str, run_dir: Path) -> dict:
    from openai import APIError, APIStatusError

    run_dir.mkdir(parents=True, exist_ok=False)
    screenshot = run_dir / "screen.png"
    image = Image.new("RGB", (320, 180), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((110, 65, 210, 115), fill="blue")
    draw.text((141, 82), "Click", fill="white")
    image.save(screenshot)
    tools = [{"type": "computer"}]
    result = {
        "model": model,
        "url": f"{config.api_base}/responses",
        "tools": tools,
        "openai_sdk": version("openai"),
        "http_status": None,
        "request_id": None,
        "error_body": None,
        "computer_call": False,
        "usage_json": str(run_dir / "usage.json"),
    }
    stage = "spark" if model == config.judge_model else "wonder"
    try:
        async with model_client(config) as client:
            response = await client.responses.with_raw_response.create(
                model=model,
                tools=tools,
                input=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "Click the blue button once."},
                            {"type": "input_image", "image_url": image_url(screenshot)},
                        ],
                    }
                ],
            )
        result["http_status"] = response.status_code
        result["request_id"] = response.headers.get("x-request-id")
        body = response.http_response.json()
        record_model_result(
            run_dir, f"{stage}_raw_0.json", {**body, "request_id": result["request_id"]}
        )
        result["computer_call"] = any(
            item.get("type") == "computer_call" for item in body.get("output", [])
        )
    except APIError as exc:
        if isinstance(exc, APIStatusError):
            result["http_status"] = exc.status_code
            result["request_id"] = exc.request_id
            try:
                result["error_body"] = exc.response.json()
            except ValueError:
                result["error_body"] = exc.response.text
        result["error_type"] = type(exc).__name__
        error_body = result["error_body"]
        record_model_result(
            run_dir,
            f"{stage}_error.json",
            {
                "model": model,
                "type": result["error_type"],
                "request_id": result["request_id"],
                "status_code": result["http_status"],
                "error_body": error_body,
                "usage": error_body.get("usage") if isinstance(error_body, dict) else None,
            },
        )
    write_json(run_dir / "result.json", result)
    return redact(result)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    config = load_config()
    if config.api_base != "https://api.meta.ai/v1" or not config.api_key.strip():
        print("Probe requires the official Meta endpoint and a configured key.")
        return 2
    run_dir = Path("runs") / f"{datetime.now(UTC):%Y%m%dT%H%M%S%f}-computer-probe"
    result = asyncio.run(probe(config, args.model, run_dir))
    print(json.dumps(result))
    return 0 if result["computer_call"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
