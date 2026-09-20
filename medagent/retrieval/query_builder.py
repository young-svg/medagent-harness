from __future__ import annotations

import re


class QueryBuilder:
    """Build a deterministic query from task context without an LLM call."""

    def build(self, question: str, subtask: str, deliverable: str, tool_input: str = "") -> str:
        parts = [question, subtask, deliverable.replace("_", " ").lower(), tool_input]
        seen: set[str] = set()
        clean: list[str] = []
        for value in parts:
            value = re.sub(r"\s+", " ", value).strip()
            key = value.casefold()
            if value and key not in seen:
                clean.append(value)
                seen.add(key)
        return " ".join(clean)
