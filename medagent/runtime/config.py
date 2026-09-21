from __future__ import annotations

import os
from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """Configuration for the self-contained public runtime."""

    mode: str = "native"
    trace_dir: str = "runs"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = "deterministic-local"
    llm_temperature: float = 0.0
    llm_max_tokens: int = 1200
    llm_timeout_seconds: float = 60.0
    max_tool_calls: int = 2
    worker_timeout_seconds: float = 180.0
    retrieval_top_k: int = 5
    retrieval_threshold: float = 0.63

    @classmethod
    def from_env(cls) -> RuntimeConfig:
        mode = os.getenv("MEDAGENT_RUNTIME_MODE", "native").strip().lower()
        if mode not in {"native", "replay"}:
            raise ValueError("MEDAGENT_RUNTIME_MODE must be 'native' or 'replay'")
        return cls(
            mode=mode,
            trace_dir=os.getenv("MEDAGENT_TRACE_DIR", "runs"),
            llm_base_url=os.getenv("MEDAGENT_LLM_BASE_URL", "").rstrip("/"),
            llm_api_key=os.getenv("MEDAGENT_LLM_API_KEY", ""),
            llm_model=os.getenv("MEDAGENT_LLM_MODEL", "deterministic-local"),
            llm_temperature=float(os.getenv("MEDAGENT_LLM_TEMPERATURE", "0")),
            llm_max_tokens=int(os.getenv("MEDAGENT_LLM_MAX_TOKENS", "1200")),
            llm_timeout_seconds=float(os.getenv("MEDAGENT_LLM_TIMEOUT_SECONDS", "60")),
            max_tool_calls=int(os.getenv("MEDAGENT_MAX_TOOL_CALLS", "2")),
            worker_timeout_seconds=float(os.getenv("MEDAGENT_WORKER_TIMEOUT_SECONDS", "180")),
            retrieval_top_k=int(os.getenv("MEDAGENT_RETRIEVAL_TOP_K", "5")),
            retrieval_threshold=float(os.getenv("MEDAGENT_RETRIEVAL_THRESHOLD", "0.63")),
        )

    def public_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["llm_api_key"] = "configured" if self.llm_api_key else ""
        return data
