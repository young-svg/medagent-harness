from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from medagent.context.evidence_spec import EvidenceRequirement
from medagent.context.request_spec import RequestItem
from medagent.planning.complexity import EVIDENCE_TOOLS
from medagent.tools.schemas import (
    RETRIEVAL_TOOLS,
    TOOL_SCHEMAS,
    ToolArgumentError,
    validate_tool_arguments,
)

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
        *, evidence_requirements: tuple[EvidenceRequirement, ...] | None = None,
    ) -> None:
        self._handlers: dict[str, Callable[..., Any]] = {}
        self._contextual_handlers: set[str] = set()
        self.max_calls = max_calls
        self._calls: dict[tuple[str, str | None], int] = {}
        self._capability_filter = capability_filter
        self._evidence_requirements = evidence_requirements

    @property
    def evidence_requirements(self) -> tuple[EvidenceRequirement, ...] | None:
        return self._evidence_requirements

    def allowed_for(
        self, worker: str, *, context: ToolExecutionContext | None = None,
    ) -> set[str]:
        role_tools = set(VISIBLE_TOOLS.get(worker, set()))
        allowed = (role_tools if self._capability_filter is None else
                   role_tools & self._capability_filter.get(worker, set()))
        if self._evidence_requirements is not None:
            ids = {item.id for item in context.request_items} if context else None
            retrieval = set().union(*(EVIDENCE_TOOLS[r.source_category]
                                      for r in self._evidence_requirements
                                      if r.execution == "retrieve"
                                      and (ids is None or r.request_item_id in ids)))
            allowed -= RETRIEVAL_TOOLS - retrieval
        return allowed

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

    def schemas_for(
        self, worker: str, *, context: ToolExecutionContext | None = None,
    ) -> list[dict[str, object]]:
        return [
            TOOL_SCHEMAS[name]
            for name in sorted(self.allowed_for(worker, context=context))
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
        if name not in self.allowed_for(worker, context=context):
            raise PermissionError(f"tool {name!r} is not visible to {worker}")
        if name not in self._handlers:
            raise LookupError(f"tool {name!r} has no configured handler")
        key = (worker, context.subtask_id if context else None)
        used = self._calls.get(key, 0)
        if used >= self.max_calls:
            raise RuntimeError(f"tool call budget exhausted for {worker}")
        self._calls[key] = used + 1
        # Rejected arguments consume an attempted call, just like handler TypeErrors.
        validate_tool_arguments(name, arguments)
        if name in RETRIEVAL_TOOLS:
            arguments = dict(arguments)
            target = arguments.pop("request_item_id", None)
            if context and context.request_items:
                items = context.request_items
                if target is None and len(items) == 1:
                    target = items[0].id
                selected = next((item for item in items if item.id == target), None)
                if selected is None:
                    raise ToolArgumentError("retrieval requires one assigned request_item_id")
                context = replace(
                    context, request_items=(selected,),
                    description=selected.text if len(items) > 1 else context.description,
                    deliverable_ids=(
                        (selected.semantic_type,) if selected.semantic_type
                        and selected.semantic_type != "UNKNOWN" else ()
                    ) if len(items) > 1 else context.deliverable_ids,
                    excluded_source_spans=context.excluded_source_spans + tuple(
                        item.source_span for item in items if item.id != selected.id
                    ),
                )
            elif target is not None:
                raise ToolArgumentError("request target requires a trusted execution context")
            if self._evidence_requirements is not None:
                if (context is None or len(context.request_items) != 1
                        or name not in self.allowed_for(worker, context=context)):
                    raise PermissionError("tool is not permitted for the target request item")
        if name in self._contextual_handlers:
            result = self._handlers[name](context, **arguments)
        else:
            result = self._handlers[name](**arguments)
        return await result if inspect.isawaitable(result) else result

    def calls_for(self, worker: str, *, subtask_id: str | None = None) -> int:
        """Role totals for compatibility; a trusted task selector gives task-local usage."""
        if subtask_id is not None:
            return self._calls.get((worker, subtask_id), 0)
        return sum(count for (role, _), count in self._calls.items() if role == worker)
