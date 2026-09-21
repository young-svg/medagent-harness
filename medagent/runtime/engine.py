from __future__ import annotations

from typing import Any, Protocol


class RuntimeConfigurationError(RuntimeError):
    """The selected public runtime cannot be configured."""


class EngineExecutionError(RuntimeError):
    """The runtime failed before returning a terminal result."""


class Engine(Protocol):
    mode: str

    async def analyze(
        self, description: str, question: str, session_id: str = "default"
    ) -> dict[str, Any]: ...

    async def close(self) -> None: ...
