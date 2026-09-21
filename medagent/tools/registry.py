from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from medagent.tools.schemas import TOOL_SCHEMAS

VISIBLE_TOOLS = {
    "consultation_agent": {
        "assess_risk",
        "clinical_guideline",
        "recommend_lifestyle",
        "search_knowledge",
    },
    "diagnostic_agent": {
        "analyze_symptoms",
        "assess_risk",
        "clinical_guideline",
        "disease_code",
        "search_knowledge",
    },
    "research_agent": {
        "clinical_guideline",
        "deep_research",
        "disease_code",
        "search_knowledge",
    },
}


class ToolRegistry:
    def __init__(
        self,
        max_calls: int = 2,
        capability_filter: dict[str, set[str]] | None = None,
    ) -> None:
        self._handlers: dict[str, Callable[..., Any]] = {}
        self.max_calls = max_calls
        self._calls: dict[str, int] = {}
        self._capability_filter = capability_filter

    def allowed_for(self, worker: str) -> set[str]:
        role_tools = set(VISIBLE_TOOLS.get(worker, set()))
        if self._capability_filter is None:
            return role_tools
        return role_tools & self._capability_filter.get(worker, set())

    def register(self, name: str, handler: Callable[..., Any]) -> None:
        if name not in TOOL_SCHEMAS:
            raise ValueError(f"unknown public tool: {name}")
        self._handlers[name] = handler

    def schemas_for(self, worker: str) -> list[dict[str, object]]:
        return [
            TOOL_SCHEMAS[name]
            for name in sorted(self.allowed_for(worker))
            if name in self._handlers
        ]

    async def execute(self, worker: str, name: str, arguments: dict[str, Any]) -> Any:
        if name not in self.allowed_for(worker):
            raise PermissionError(f"tool {name!r} is not visible to {worker}")
        if name not in self._handlers:
            raise LookupError(f"tool {name!r} has no configured handler")
        used = self._calls.get(worker, 0)
        if used >= self.max_calls:
            raise RuntimeError(f"tool call budget exhausted for {worker}")
        self._calls[worker] = used + 1
        result = self._handlers[name](**arguments)
        return await result if inspect.isawaitable(result) else result

    def calls_for(self, worker: str) -> int:
        return self._calls.get(worker, 0)
