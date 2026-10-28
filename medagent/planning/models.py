from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

VALID_WORKERS = {"diagnostic_agent", "consultation_agent", "research_agent"}


class PlanDependencyError(ValueError):
    """A plan cannot safely express or preserve its execution dependencies."""


@dataclass(slots=True)
class Subtask:
    subtask_id: str
    description: str
    assigned_agent: str
    deliverable_ids: list[str] = field(default_factory=list)
    justification: str = ""
    request_item_ids: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)

    def validate(self) -> None:
        if not self.subtask_id.strip() or not self.description.strip():
            raise ValueError("subtask id and description are required")
        if self.assigned_agent not in VALID_WORKERS:
            raise ValueError("subtask is not dispatchable")
        if not isinstance(self.depends_on, list) or any(
            not isinstance(value, str) or not value.strip() for value in self.depends_on
        ):
            raise PlanDependencyError("depends_on must be a list of non-empty task IDs")
        if len(set(self.depends_on)) != len(self.depends_on):
            raise PlanDependencyError("duplicate dependency IDs")

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
        by_id = {item.subtask_id: item for item in self.subtasks}
        if len(by_id) != len(self.subtasks):
            raise PlanDependencyError("task IDs must be unique")
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(task_id: str) -> None:
            if task_id in visiting:
                raise PlanDependencyError("cyclic task dependency")
            if task_id in visited:
                return
            visiting.add(task_id)
            for source in by_id[task_id].depends_on:
                if source not in by_id:
                    raise PlanDependencyError("unknown dependency task ID")
                if source == task_id:
                    raise PlanDependencyError("task cannot depend on itself")
                visit(source)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in by_id:
            visit(task_id)

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
