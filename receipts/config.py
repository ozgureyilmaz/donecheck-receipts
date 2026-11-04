"""Configuration shared by the local environment and Meta clients."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    api_key: str = field(repr=False)
    api_base: str
    agent_model: str
    judge_model: str
    sam_model: str
    mocksite_host: str
    mocksite_port: int
    db_path: Path
    max_steps: int
    api_timeout: float

    @property
    def site_url(self) -> str:
        return f"http://127.0.0.1:{self.mocksite_port}"


def load_config(env_file: str | Path = ".env") -> Config:
    load_dotenv(env_file, override=False)
    host = os.getenv("MOCKSITE_HOST", "127.0.0.1")
    if host != "127.0.0.1":
        raise ValueError("MOCKSITE_HOST must be 127.0.0.1: the site is local only")
    port = int(os.getenv("MOCKSITE_PORT", "8000"))
    steps = int(os.getenv("RECEIPTS_MAX_STEPS", "40"))
    timeout = float(os.getenv("RECEIPTS_API_TIMEOUT", "90"))
    if not (1 <= port <= 65535 and 1 <= steps <= 500 and timeout > 0):
        raise ValueError("Invalid port, step cap, or timeout")
    return Config(
        api_key=os.getenv("MODEL_API_KEY") or os.getenv("META_API_KEY", ""),
        api_base=os.getenv("META_API_BASE", "https://api.meta.ai/v1").rstrip("/"),
        agent_model=os.getenv("RECEIPTS_AGENT_MODEL", "muse-wonder-1.0"),
        judge_model=os.getenv("RECEIPTS_JUDGE_MODEL", "muse-spark-1.3"),
        sam_model=os.getenv("RECEIPTS_SAM_MODEL", "sam-3.1"),
        mocksite_host=host,
        mocksite_port=port,
        db_path=Path(os.getenv("RECEIPTS_DB", "runs/receipts.sqlite")),
        max_steps=steps,
        api_timeout=timeout,
    )


def model_client(config: Config):
    from openai import AsyncOpenAI

    if not config.api_key:
        raise RuntimeError("MODEL_API_KEY (or META_API_KEY) is not configured")
    return AsyncOpenAI(
        base_url=config.api_base,
        api_key=config.api_key,
        timeout=config.api_timeout,
        max_retries=0,
    )
