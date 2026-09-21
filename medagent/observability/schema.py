from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

EVENT_TYPES = {
    "run_start",
    "context_built",
    "memory_read",
    "request_spec_built",
    "contract_built",
    "complexity_profile_built",
    "response_profile_built",
    "evidence_ledger_built",
    "plan_created",
    "route_selected",
    "capability_policy_applied",
    "llm_request",
    "llm_response",
    "generation_summary",
    "worker_provider_attempt",
    "tool_call",
    "tool_result",
    "retrieval_query",
    "retrieval_result",
    "worker_draft",
    "contract_coverage",
    "request_coverage",
    "synthesis_input",
    "synthesis_output",
    "checker_input",
    "checker_result",
    "patch_applied",
    "memory_write",
    "final_answer",
    "run_end",
    "error",
}


@dataclass(slots=True)
class TraceEvent:
    run_id: str
    stage: str
    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    agent: str | None = None
    parent_event_id: str | None = None
    event_id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def __post_init__(self) -> None:
        if self.event_type not in EVENT_TYPES:
            raise ValueError(f"unknown trace event: {self.event_type}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
