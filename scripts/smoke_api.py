"""Bounded Meta connectivity smoke; never prints credentials or response headers."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from receipts.audit.db import redact
from receipts.config import load_config, model_client
from receipts.usage import record_model_result


async def main(runs_dir: Path = Path("runs")) -> int:
    config = load_config()
    async with httpx.AsyncClient(timeout=15) as http:
        try:
            response = await http.get(f"{config.api_base}/status")
            print(f"GET /v1/status: HTTP {response.status_code}")
        except httpx.HTTPError as exc:
            print(f"GET /v1/status: UNSURE ({type(exc).__name__})")
    if not config.api_key:
        print("UNSURE: no MODEL_API_KEY or META_API_KEY; Spark and model list skipped.")
        return 2
    run_dir = runs_dir / f"{datetime.now(UTC):%Y%m%dT%H%M%S%f}-api-smoke"
    async with model_client(config) as client:
        try:
            reply = await client.responses.create(
                model=config.judge_model, input="Reply with the single word: ready"
            )
        except Exception as exc:
            record_model_result(
                run_dir,
                "spark_error.json",
                {
                    "type": type(exc).__name__,
                    "model": config.judge_model,
                    "request_id": getattr(exc, "request_id", None),
                    "status_code": getattr(exc, "status_code", None),
                },
            )
            print(f"UNSURE: Spark smoke failed ({type(exc).__name__}).")
            return 1
        record_model_result(run_dir, "spark_raw_0.json", reply.model_dump(mode="json"))
        try:
            # A deliberately restricted output prevents accidental secret echo.
            ready = reply.output_text.strip().lower().strip('."') == "ready"
            print(f"Spark ready: {ready}")
            models = await client.models.list()
            print("Models: " + json.dumps(redact([m.id for m in models.data])))
            return 0 if ready else 1
        except Exception as exc:
            print(f"UNSURE: Meta smoke failed ({type(exc).__name__}); no automatic retry.")
            return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
