from __future__ import annotations

from typing import Any, Protocol


class RuntimeConfigurationError(RuntimeError):
    """The selected public runtime cannot be configured."""


class EngineExecutionError(RuntimeError):
    """The runtime failed before returning a terminal result."""


class StageGenerationError(EngineExecutionError):
    """A named runtime stage could not produce a usable bounded generation."""

    def __init__(self, stage: str, reason: str) -> None:
        self.failure_stage = stage
        self.failure_reason = reason
        super().__init__(f"{stage} generation failed: {reason}")


class Engine(Protocol):
    mode: str

    async def analyze(
        self, description: str, question: str, session_id: str = "default"
    ) -> dict[str, Any]: ...

    async def close(self) -> None: ...
