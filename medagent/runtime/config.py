from __future__ import annotations

import os
from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = "deepseek-v4-flash"
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    generic_collection: str = "medical_knowledge_v1"
    special_collection: str = "medical_knowledge"
    milvus_uri: str = ""
    swarm_timeout: float = 180.0
    max_tool_calls: int = 2
    trace_dir: str = "runs"
    long_term_memory_enabled: bool = False

    @classmethod
    def from_env(cls) -> RuntimeConfig:
        return cls(
            llm_base_url=os.getenv("MEDAGENT_LLM_BASE_URL", ""),
            llm_api_key=os.getenv("MEDAGENT_LLM_API_KEY", ""),
            llm_model=os.getenv("MEDAGENT_LLM_MODEL", "deepseek-v4-flash"),
            embedding_model=os.getenv("MEDAGENT_EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5"),
            generic_collection=os.getenv("MEDAGENT_GENERIC_COLLECTION", "medical_knowledge_v1"),
            special_collection=os.getenv("MEDAGENT_SPECIAL_COLLECTION", "medical_knowledge"),
            milvus_uri=os.getenv("MEDAGENT_MILVUS_URI", ""),
            swarm_timeout=float(os.getenv("MEDAGENT_SWARM_TIMEOUT", "180")),
            max_tool_calls=int(os.getenv("MEDAGENT_MAX_TOOL_CALLS", "2")),
            trace_dir=os.getenv("MEDAGENT_TRACE_DIR", "runs"),
            long_term_memory_enabled=False,
        )

    def public_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["llm_api_key"] = "***" if self.llm_api_key else ""
        return data
