from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from math import isfinite


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """Configuration for the self-contained public runtime."""

    mode: str = "native"
    trace_dir: str = "runs"
    trace_content_mode: str = "metadata"
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
    worker_max_model_rounds: int = 8
    max_subtasks: int = 16
    max_concurrent_workers: int = 4
    worker_timeout_seconds: float = 180.0
    retrieval_mode: str = "off"
    milvus_uri: str = ""
    milvus_token: str = ""
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    generic_collection: str = "clinical_knowledge"
    special_collection: str = "clinical_guidelines"
    retrieval_top_k: int = 5
    retrieval_threshold: float = 0.63
    memory_token_budget: int | None = 4096
    semantic_validation_enabled: bool = True
    semantic_max_calls: int = 3
    semantic_max_tokens: int = 4096
    semantic_timeout_seconds: float = 30.0
    semantic_max_input_chars: int = 60000

    def __post_init__(self) -> None:
        for name, low, high in (
            ("worker_max_model_rounds", 1, 32), ("max_subtasks", 1, 64),
            ("max_concurrent_workers", 1, 16), ("max_tool_calls", 0, 16),
        ):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{name} must be an integer between {low} and {high}")
        for name in ("worker_timeout_seconds", "llm_timeout_seconds"):
            value = getattr(self, name)
            if (type(value) not in {int, float} or not isfinite(value) or not 0 < value <= 900):
                raise ValueError(f"{name} must be finite and in (0, 900]")
        for name in ("planner_max_length_recoveries", "worker_max_length_recoveries",
                     "synthesis_max_length_recoveries"):
            if type(getattr(self, name)) is not int or getattr(self, name) not in {0, 1}:
                raise ValueError(f"{name} must be 0 or 1")
        if self.trace_content_mode not in {"metadata", "raw"}:
            raise ValueError("trace_content_mode must be metadata or raw")
        if type(self.semantic_validation_enabled) is not bool:
            raise ValueError("semantic_validation_enabled must be a boolean")
        if type(self.semantic_max_calls) is not int or not 0 <= self.semantic_max_calls <= 3:
            raise ValueError("semantic_max_calls must be between 0 and 3")
        if type(self.semantic_max_tokens) is not int or not 128 <= self.semantic_max_tokens <= 8192:
            raise ValueError("semantic_max_tokens must be between 128 and 8192")
        if (type(self.semantic_timeout_seconds) not in {int, float}
                or not 0 < self.semantic_timeout_seconds <= 60):
            raise ValueError("semantic_timeout_seconds must be in (0, 60]")
        if type(self.semantic_max_input_chars) is not int or self.semantic_max_input_chars < 1:
            raise ValueError("semantic_max_input_chars must be positive")
        if (type(self.worker_max_infrastructure_retries) is not int
                or not 0 <= self.worker_max_infrastructure_retries <= 3):
            raise ValueError("worker_max_infrastructure_retries must be between 0 and 3")
        if not 0 <= self.worker_infrastructure_retry_base_delay_seconds <= 30:
            raise ValueError(
                "worker_infrastructure_retry_base_delay_seconds must be between 0 and 30"
            )
        if self.memory_token_budget is not None and (
            type(self.memory_token_budget) is not int or self.memory_token_budget < 0
        ):
            raise ValueError("memory_token_budget must be a non-negative integer or None")

    @classmethod
    def from_env(cls) -> RuntimeConfig:
        mode = os.getenv("MEDAGENT_RUNTIME_MODE", "native").strip().lower()
        if mode not in {"native", "replay"}:
            raise ValueError("MEDAGENT_RUNTIME_MODE must be 'native' or 'replay'")
        retrieval_mode = os.getenv("MEDAGENT_RETRIEVAL_MODE", "off").strip().lower()
        if retrieval_mode not in {"off", "fake", "milvus"}:
            raise ValueError("MEDAGENT_RETRIEVAL_MODE must be 'off', 'fake', or 'milvus'")
        memory_budget = os.getenv("MEDAGENT_MEMORY_TOKEN_BUDGET", "4096").strip()
        semantic_enabled = os.getenv("MEDAGENT_SEMANTIC_VALIDATION", "true").strip().lower()
        if semantic_enabled not in {"true", "false"}:
            raise ValueError("MEDAGENT_SEMANTIC_VALIDATION must be true or false")
        return cls(
            mode=mode,
            trace_dir=os.getenv("MEDAGENT_TRACE_DIR", "runs"),
            trace_content_mode=os.getenv("MEDAGENT_TRACE_CONTENT", "metadata").strip().lower(),
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
            worker_max_model_rounds=int(os.getenv("MEDAGENT_WORKER_MAX_MODEL_ROUNDS", "8")),
            max_subtasks=int(os.getenv("MEDAGENT_MAX_SUBTASKS", "16")),
            max_concurrent_workers=int(os.getenv("MEDAGENT_MAX_CONCURRENT_WORKERS", "4")),
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
            memory_token_budget=None if memory_budget.casefold() == "none" else int(memory_budget),
            semantic_validation_enabled=semantic_enabled == "true",
        )

    def public_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["llm_api_key"] = "configured" if self.llm_api_key else ""
        data["milvus_token"] = "configured" if self.milvus_token else ""
        return data
