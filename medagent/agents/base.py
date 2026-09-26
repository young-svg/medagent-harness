from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    agent_id: str
    role: str
    scope: str
    safety_boundary: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RequestItemAnswer:
    request_item_id: str
    answer: str

    def validate(self) -> None:
        if not self.request_item_id.strip() or not self.answer.strip():
            raise ValueError("request item id and answer are required")

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
    infrastructure_retry_count: int = 0
    provider_attempt_count: int = 0
    protocol_recovery_count: int = 0
    protocol_recovery_success_count: int = 0
    worker_status: str = "success"
    request_item_answers: list[RequestItemAnswer] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["answered_request_item_ids"] = [
            item.request_item_id for item in self.request_item_answers if item.answer.strip()
        ]
        return value
