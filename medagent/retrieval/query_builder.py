from __future__ import annotations

import re
from collections.abc import Sequence

from medagent.context.request_spec import RequestItem


class QueryBuilder:
    """Build a deterministic query from task context without an LLM call."""

    def build(
        self,
        question: str,
        subtask: str,
        deliverable: str | Sequence[str],
        tool_input: str = "",
        *,
        request_items: Sequence[RequestItem] = (),
        excluded_source_spans: Sequence[str] = (),
    ) -> str:
        # Remove only explicit unassigned RequestSpec spans, not inferred medical
        # concepts. Shared question preamble and all assigned source text survive.
        for span in sorted(set(excluded_source_spans), key=len, reverse=True):
            if span and not any(span in item.source_span for item in request_items):
                question = question.replace(span, " ")
        deliverables = [deliverable] if isinstance(deliverable, str) else deliverable
        parts = [question, subtask]
        parts.extend(value.replace("_", " ").lower() for value in deliverables)
        # Unsplit questions may themselves be RequestItems; do not repeat them.
        parts.extend(item.text for item in request_items if item.text not in question)
        parts.append(tool_input)
        seen: set[str] = set()
        clean: list[str] = []
        for value in parts:
            value = re.sub(r"\s+", " ", value).strip()
            key = value.casefold()
            if value and key not in seen:
                clean.append(value)
                seen.add(key)
        return " ".join(clean)
