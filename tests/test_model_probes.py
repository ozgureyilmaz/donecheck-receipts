import asyncio
import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from openai import AsyncOpenAI

from receipts.config import Config


def load_script(name):
    path = Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py"
    spec = spec_from_file_location(name, path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


repro_computer_tool = load_script("repro_computer_tool")
smoke_api = load_script("smoke_api")


@pytest.fixture
def config(tmp_path, monkeypatch):
    key = "fixture-only-model-key"
    monkeypatch.setenv("MODEL_API_KEY", key)
    return Config(
        api_key=key,
        api_base="https://api.meta.ai/v1",
        agent_model="muse-wonder-1.0",
        judge_model="muse-spark-1.3",
        sam_model="sam-3.1",
        mocksite_host="127.0.0.1",
        mocksite_port=8000,
        db_path=tmp_path / "audit.sqlite",
        max_steps=30,
        api_timeout=5,
    )


USAGE = {
    "input_tokens": 20,
    "input_tokens_details": {"cached_tokens": 10},
    "output_tokens": 5,
    "total_tokens": 25,
}


@pytest.mark.parametrize("status", [200, 400])
def test_probe_records_one_response_with_redacted_error_or_usage(
    tmp_path, monkeypatch, config, status
):
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        assert str(request.url) == "https://api.meta.ai/v1/responses"
        if status == 400:
            payload = {"error": {"message": f"Unsupported tool. {config.api_key}"}}
        else:
            payload = {
                "id": "resp_fixture",
                "model": config.judge_model,
                "status": "completed",
                "output": [{"type": "computer_call"}],
                "usage": USAGE,
            }
        return httpx.Response(status, headers={"x-request-id": "req_fixture"}, json=payload)

    def client(cfg):
        return AsyncOpenAI(
            base_url=cfg.api_base,
            api_key=cfg.api_key,
            max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        )

    monkeypatch.setattr(repro_computer_tool, "model_client", client)
    run = tmp_path / "probe"
    result = asyncio.run(repro_computer_tool.probe(config, config.judge_model, run))
    assert len(requests) == 1
    assert requests[0]["tools"] == [{"type": "computer"}]
    assert requests[0]["input"][0]["content"][1]["image_url"].startswith("data:image/png;base64,")
    assert result["http_status"] == status
    assert result["request_id"] == "req_fixture"
    assert result["computer_call"] is (status == 200)
    ledger = json.loads((run / "usage.json").read_text())
    if status == 200:
        assert ledger["calls"][0]["total_tokens"] == 25
        assert ledger["calls"][0]["estimated_cost_usd"] == "0.00003525"
    else:
        assert result["error_body"] == {"error": {"message": "Unsupported tool. [REDACTED]"}}
        assert ledger["totals"]["unknown_cost_calls"] == 1
        assert config.api_key not in (run / "spark_error.json").read_text()


def test_smoke_saves_usage_before_listing_models(tmp_path, monkeypatch, config, capsys):
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def get(self, _url):
            return SimpleNamespace(status_code=200)

        @property
        def responses(self):
            return SimpleNamespace(create=self.create)

        @property
        def models(self):
            return SimpleNamespace(list=self.list_models)

        async def create(self, **_):
            raw = {
                "id": "resp_smoke",
                "model": config.judge_model,
                "status": "completed",
                "usage": USAGE,
            }
            return SimpleNamespace(output_text="ready", model_dump=lambda **_: raw)

        async def list_models(self):
            ledger_path = next(tmp_path.glob("*-api-smoke/usage.json"))
            ledger = json.loads(ledger_path.read_text())
            assert ledger["calls"][0]["cached_input_tokens"] == 10
            return SimpleNamespace(data=[SimpleNamespace(id=config.judge_model)])

    monkeypatch.setattr(smoke_api, "load_config", lambda: config)
    monkeypatch.setattr(smoke_api, "model_client", lambda _: Client())
    monkeypatch.setattr(smoke_api.httpx, "AsyncClient", lambda **_: Client())
    assert asyncio.run(smoke_api.main(tmp_path)) == 0
    assert capsys.readouterr().out.splitlines() == [
        "GET /v1/status: HTTP 200",
        "Spark ready: True",
        'Models: ["muse-spark-1.3"]',
    ]
