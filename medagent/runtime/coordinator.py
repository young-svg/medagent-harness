from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from medagent.llm.client import LLMClient
from medagent.memory.session import SessionMemory
from medagent.retrieval.backend import RetrievalBackend
from medagent.runtime.config import RuntimeConfig
from medagent.runtime.engine import Engine, RuntimeConfigurationError
from medagent.runtime.native_engine import NativeMedAgentEngine


class Coordinator:
    """Public facade for native execution."""

    def __init__(
        self,
        config: RuntimeConfig | None = None,
        *,
        llm: LLMClient | None = None,
        memory: SessionMemory | None = None,
        backend: RetrievalBackend | None = None,
    ) -> None:
        self.config = config or RuntimeConfig.from_env()
        if self.config.mode != "native":
            raise RuntimeConfigurationError(
                "replay is read-only; use `medagent replay RUN_DIR` for saved traces"
            )
        self.engine: Engine = NativeMedAgentEngine(
            self.config, llm=llm, retrieval_backend=backend, memory=memory
        )

    @property
    def mode(self) -> str:
        return self.engine.mode

    async def analyze(
        self, description: str, question: str, session_id: str = "default"
    ) -> dict[str, Any]:
        return await self.engine.analyze(description, question, session_id)

    async def close(self) -> None:
        await self.engine.close()


async def analyze_case(
    description: str,
    question: str,
    session_id: str = "default",
    trace_dir: str | Path | None = None,
) -> dict[str, Any]:
    config = RuntimeConfig.from_env()
    if trace_dir is not None:
        config = replace(config, trace_dir=str(trace_dir))
    coordinator = Coordinator(config=config)
    try:
        return await coordinator.analyze(description, question, session_id)
    finally:
        await coordinator.close()
