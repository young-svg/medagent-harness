from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

EVENT_TYPES = {
    "run_start",
    "context",
    "memory",
    "contract",
    "ledger",
    "plan",
    "route",
    "llm_request",
    "llm_response",
    "tool_call",
    "tool_result",
    "retrieval_query",
    "retrieval_result",
    "worker_draft",
    "synthesis",
    "checker",
    "patch",
    "final_answer",
    "run_end",
    "error",
}


@dataclass(slots=True)
class TraceEvent:
    event: str
    run_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    parent_id: str | None = None

    def __post_init__(self) -> None:
        if self.event not in EVENT_TYPES:
            raise ValueError(f"unknown trace event: {self.event}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
