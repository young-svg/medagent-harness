from __future__ import annotations

from collections.abc import Callable
from typing import Any

from medagent.tools.schemas import TOOL_SCHEMAS

VISIBLE_TOOLS = {
    "consultation_agent": {
        "assess_risk",
        "recommend_lifestyle",
        "search_history",
        "search_knowledge",
    },
    "diagnostic_agent": {
        "analyze_symptoms",
        "assess_risk",
        "clinical_guideline",
        "disease_code",
        "search_history",
        "search_knowledge",
    },
    "research_agent": {"clinical_guideline", "deep_research", "search_history", "search_knowledge"},
}


class ToolRegistry:
    def __init__(self, max_calls: int = 2) -> None:
        self._handlers: dict[str, Callable[..., Any]] = {}
        self.max_calls = max_calls
        self._calls: dict[str, int] = {}

    def register(self, name: str, handler: Callable[..., Any]) -> None:
        if name == "search_similar_cases":
            raise ValueError("search_similar_cases is EXPERIMENTAL_NOT_EXPOSED")
        self._handlers[name] = handler

    def schemas_for(self, worker: str) -> list[dict[str, object]]:
        return [TOOL_SCHEMAS[name] for name in sorted(VISIBLE_TOOLS.get(worker, set()))]

    def execute(self, worker: str, name: str, arguments: dict[str, Any]) -> Any:
        if name not in VISIBLE_TOOLS.get(worker, set()):
            raise PermissionError(f"tool {name!r} is not visible to {worker}")
        if name not in self._handlers:
            raise LookupError(f"tool {name!r} has no configured handler")
        used = self._calls.get(worker, 0)
        if used >= self.max_calls:
            raise RuntimeError(f"tool call budget exhausted for {worker}")
        self._calls[worker] = used + 1
        return self._handlers[name](**arguments)
