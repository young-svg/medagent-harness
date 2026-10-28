from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from medagent.context.request_spec import RequestItem
from medagent.tools.schemas import TOOL_SCHEMAS, validate_tool_arguments

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


@dataclass(frozen=True, slots=True)
class ToolExecutionContext:
    """Trusted per-call snapshot, never part of model-generated arguments."""

    subtask_id: str
    description: str
    deliverable_ids: tuple[str, ...]
    request_items: tuple[RequestItem, ...]
    excluded_source_spans: tuple[str, ...] = ()


class ToolRegistry:
    def __init__(
        self,
        max_calls: int = 2,
        capability_filter: dict[str, set[str]] | None = None,
    ) -> None:
        self._handlers: dict[str, Callable[..., Any]] = {}
        self._contextual_handlers: set[str] = set()
        self.max_calls = max_calls
        self._calls: dict[str, int] = {}
        self._capability_filter = capability_filter

    def allowed_for(self, worker: str) -> set[str]:
        role_tools = set(VISIBLE_TOOLS.get(worker, set()))
        if self._capability_filter is None:
            return role_tools
        return role_tools & self._capability_filter.get(worker, set())

    def register(
        self, name: str, handler: Callable[..., Any], *, with_context: bool = False
    ) -> None:
        if name not in TOOL_SCHEMAS:
            raise ValueError(f"unknown public tool: {name}")
        self._handlers[name] = handler
        if with_context:
            self._contextual_handlers.add(name)
        else:
            self._contextual_handlers.discard(name)

    def schemas_for(self, worker: str) -> list[dict[str, object]]:
        return [
            TOOL_SCHEMAS[name]
            for name in sorted(self.allowed_for(worker))
            if name in self._handlers
        ]

    async def execute(
        self,
        worker: str,
        name: str,
        arguments: object,
        *,
        context: ToolExecutionContext | None = None,
    ) -> Any:
        if name not in self.allowed_for(worker):
            raise PermissionError(f"tool {name!r} is not visible to {worker}")
        if name not in self._handlers:
            raise LookupError(f"tool {name!r} has no configured handler")
        used = self._calls.get(worker, 0)
        if used >= self.max_calls:
            raise RuntimeError(f"tool call budget exhausted for {worker}")
        self._calls[worker] = used + 1
        # Rejected arguments consume an attempted call, just like handler TypeErrors.
        validate_tool_arguments(name, arguments)
        if name in self._contextual_handlers:
            result = self._handlers[name](context, **arguments)
        else:
            result = self._handlers[name](**arguments)
        return await result if inspect.isawaitable(result) else result

    def calls_for(self, worker: str) -> int:
        return self._calls.get(worker, 0)
