from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

VALID_WORKERS = {"diagnostic_agent", "consultation_agent", "research_agent"}


@dataclass(slots=True)
class Subtask:
    subtask_id: str
    description: str
    assigned_agent: str

    def validate(self) -> None:
        if not self.subtask_id.strip() or not self.description.strip():
            raise ValueError("subtask id and description are required")
        if self.assigned_agent not in VALID_WORKERS:
            raise ValueError("subtask is not dispatchable")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Plan:
    subtasks: list[Subtask]
    fallback_reason: str | None = None

    def validate(self) -> None:
        if not self.subtasks:
            raise ValueError("plan requires a subtask")
        for subtask in self.subtasks:
            subtask.validate()

    def to_dict(self) -> dict[str, Any]:
        return {
            "subtasks": [item.to_dict() for item in self.subtasks],
            "fallback_reason": self.fallback_reason,
        }
