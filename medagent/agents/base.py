from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    agent_id: str
    role: str
    scope: str
    safety_boundary: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(slots=True)
class WorkerResult:
    worker: str
    subtask_id: str
    answer: str
    success: bool = True
    tool_calls: int = 0
    generation_status: str = "completed_first_attempt"
    length_recovery_count: int = 0
    failure_reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
