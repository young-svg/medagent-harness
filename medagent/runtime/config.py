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
    planner_max_tokens: int = 8192
    planner_max_length_recoveries: int = 1
    worker_max_tokens: int = 8192
    worker_max_length_recoveries: int = 1
    worker_max_infrastructure_retries: int = 2
    worker_infrastructure_retry_base_delay_seconds: float = 1.0
    synthesis_max_tokens: int = 8192
    synthesis_max_length_recoveries: int = 1
    llm_timeout_seconds: float = 60.0
    max_tool_calls: int = 2
    worker_timeout_seconds: float = 180.0
    retrieval_mode: str = "off"
    milvus_uri: str = ""
    milvus_token: str = ""
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    generic_collection: str = "clinical_knowledge"
    special_collection: str = "clinical_guidelines"
    retrieval_top_k: int = 5
    retrieval_threshold: float = 0.63

    def __post_init__(self) -> None:
        if not 0 <= self.worker_max_infrastructure_retries <= 3:
            raise ValueError("worker_max_infrastructure_retries must be between 0 and 3")
        if not 0 <= self.worker_infrastructure_retry_base_delay_seconds <= 30:
            raise ValueError(
                "worker_infrastructure_retry_base_delay_seconds must be between 0 and 30"
            )

    @classmethod
    def from_env(cls) -> RuntimeConfig:
        mode = os.getenv("MEDAGENT_RUNTIME_MODE", "native").strip().lower()
        if mode not in {"native", "replay"}:
            raise ValueError("MEDAGENT_RUNTIME_MODE must be 'native' or 'replay'")
        retrieval_mode = os.getenv("MEDAGENT_RETRIEVAL_MODE", "off").strip().lower()
        if retrieval_mode not in {"off", "fake", "milvus"}:
            raise ValueError("MEDAGENT_RETRIEVAL_MODE must be 'off', 'fake', or 'milvus'")
        return cls(
            mode=mode,
            trace_dir=os.getenv("MEDAGENT_TRACE_DIR", "runs"),
            llm_base_url=os.getenv("MEDAGENT_LLM_BASE_URL", "").rstrip("/"),
            llm_api_key=os.getenv("MEDAGENT_LLM_API_KEY", ""),
            llm_model=os.getenv("MEDAGENT_LLM_MODEL", "deterministic-local"),
            llm_temperature=float(os.getenv("MEDAGENT_LLM_TEMPERATURE", "0")),
            llm_max_tokens=int(os.getenv("MEDAGENT_LLM_MAX_TOKENS", "1200")),
            planner_max_tokens=int(os.getenv("MEDAGENT_PLANNER_MAX_TOKENS", "8192")),
            planner_max_length_recoveries=int(
                os.getenv("MEDAGENT_PLANNER_MAX_LENGTH_RECOVERIES", "1")
            ),
            worker_max_tokens=int(os.getenv("MEDAGENT_WORKER_MAX_TOKENS", "8192")),
            worker_max_length_recoveries=int(
                os.getenv("MEDAGENT_WORKER_MAX_LENGTH_RECOVERIES", "1")
            ),
            worker_max_infrastructure_retries=int(
                os.getenv("MEDAGENT_WORKER_MAX_INFRA_RETRIES", "2")
            ),
            worker_infrastructure_retry_base_delay_seconds=float(
                os.getenv("MEDAGENT_WORKER_INFRA_RETRY_BASE_DELAY_SECONDS", "1")
            ),
            synthesis_max_tokens=int(
                os.getenv("MEDAGENT_SYNTHESIS_MAX_TOKENS", "8192")
            ),
            synthesis_max_length_recoveries=int(
                os.getenv("MEDAGENT_SYNTHESIS_MAX_LENGTH_RECOVERIES", "1")
            ),
            llm_timeout_seconds=float(os.getenv("MEDAGENT_LLM_TIMEOUT_SECONDS", "60")),
            max_tool_calls=int(os.getenv("MEDAGENT_MAX_TOOL_CALLS", "2")),
            worker_timeout_seconds=float(os.getenv("MEDAGENT_WORKER_TIMEOUT_SECONDS", "180")),
            retrieval_mode=retrieval_mode,
            milvus_uri=os.getenv("MEDAGENT_MILVUS_URI", ""),
            milvus_token=os.getenv("MEDAGENT_MILVUS_TOKEN", ""),
            embedding_model=os.getenv(
                "MEDAGENT_EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5"
            ),
            generic_collection=os.getenv(
                "MEDAGENT_GENERIC_COLLECTION", "clinical_knowledge"
            ),
            special_collection=os.getenv(
                "MEDAGENT_SPECIAL_COLLECTION", "clinical_guidelines"
            ),
            retrieval_top_k=int(os.getenv("MEDAGENT_RETRIEVAL_TOP_K", "5")),
            retrieval_threshold=float(os.getenv("MEDAGENT_RETRIEVAL_THRESHOLD", "0.63")),
        )

    def public_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["llm_api_key"] = "configured" if self.llm_api_key else ""
        data["milvus_token"] = "configured" if self.milvus_token else ""
        return data
