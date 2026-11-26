import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class AgentSettings:
    llm_api_key: str = field(repr=False)
    llm_model: str
    llm_base_url: str
    llm_timeout: float
    monitoring_url: str
    monitoring_timeout: float
    port: int


def load_settings() -> AgentSettings:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    return AgentSettings(
        llm_api_key=os.getenv("LLM_API_KEY", "").strip(),
        llm_model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
        llm_base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
        llm_timeout=float(os.getenv("LLM_TIMEOUT_SECONDS", "20")),
        monitoring_url=os.getenv("MONITORING_API_BASE_URL", "http://127.0.0.1:8001"),
        monitoring_timeout=float(os.getenv("MONITORING_TIMEOUT_SECONDS", "5")),
        port=int(os.getenv("AGENT_PORT", "8000")),
    )
