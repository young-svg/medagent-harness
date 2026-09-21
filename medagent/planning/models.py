from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

VALID_WORKERS = {"diagnostic_agent", "consultation_agent", "research_agent"}


@dataclass(slots=True)
class Subtask:
    subtask_id: str
    description: str
    assigned_agent: str
    deliverable_ids: list[str] = field(default_factory=list)
    justification: str = ""
    request_item_ids: list[str] = field(default_factory=list)

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
    planner_parse_status: str = "direct_json"
    planner_generation_status: str | None = "completed_first_attempt"
    planner_length_recovery_count: int = 0
    policy_actions: list[str] = field(default_factory=list)

    @property
    def planner_parse_failure(self) -> bool:
        return self.planner_parse_status == "fallback"

    def validate(self) -> None:
        if not self.subtasks:
            raise ValueError("plan requires a subtask")
        for subtask in self.subtasks:
            subtask.validate()

    def to_dict(self) -> dict[str, Any]:
        return {
            "subtasks": [item.to_dict() for item in self.subtasks],
            "fallback_reason": self.fallback_reason,
            "planner_parse_status": self.planner_parse_status,
            "planner_parse_failure": self.planner_parse_failure,
            "planner_generation_status": self.planner_generation_status,
            "planner_length_recovery_count": self.planner_length_recovery_count,
            "policy_actions": self.policy_actions,
        }
