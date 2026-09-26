from __future__ import annotations

import json
import re
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from medagent.observability.schema import TraceEvent

_REDACTED = "[REDACTED]"
_SENSITIVE_KEYS = {
    "authorization",
    "api_key",
    "apikey",
    "cookie",
    "password",
    "secret",
    "token",
    "access_token",
    "bearer_token",
}
_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(authorization|api[_-]?key|cookie|password|secret)\b"
    r"[\"']?\s*[:=]\s*[\"']?([^\"'\s,;}]+)"
)


def redact_trace_value(value: Any, key: str | None = None) -> Any:
    """Remove credential-shaped values while preserving ordinary clinical text."""

    normalized_key = (key or "").casefold().replace("-", "_")
    if normalized_key in _SENSITIVE_KEYS or any(
        normalized_key.endswith(f"_{suffix}")
        for suffix in ("authorization", "api_key", "cookie", "password", "secret", "token")
    ):
        return _REDACTED
    if isinstance(value, dict):
        return {
            item_key: redact_trace_value(item, str(item_key))
            for item_key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_trace_value(item) for item in value]
    if isinstance(value, tuple):
        return [redact_trace_value(item) for item in value]
    if isinstance(value, str):
        clean = _BEARER_PATTERN.sub("Bearer [REDACTED]", value)
        return _ASSIGNMENT_PATTERN.sub(lambda match: f"{match.group(1)}={_REDACTED}", clean)
    return value


class TraceRecorder:
    def __init__(self, root: str | Path, run_id: str | None = None) -> None:
        self.run_id = run_id or str(uuid4())
        self.run_dir = Path(root) / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.run_dir / "trace.jsonl"
        self.started = perf_counter()
        self.events: list[TraceEvent] = []

    def record(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        stage: str | None = None,
        agent: str | None = None,
        parent_event_id: str | None = None,
    ) -> str:
        if parent_event_id is None and self.events:
            parent_event_id = self.events[-1].event_id
        event = TraceEvent(
            run_id=self.run_id,
            stage=stage or event_type,
            event_type=event_type,
            agent=agent,
            parent_event_id=parent_event_id,
            payload=redact_trace_value(payload or {}),
        )
        self.events.append(event)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
        return event.event_id

    def finish(self, status: str = "completed") -> str:
        return self.record(
            "run_end",
            {"status": status, "latency_ms": round((perf_counter() - self.started) * 1000, 2)},
            stage="lifecycle",
        )

    def summary(self) -> dict[str, Any]:
        end = next(
            (event for event in reversed(self.events) if event.event_type == "run_end"), None
        )
        usage = [
            event.payload.get("usage", {})
            for event in self.events
            if event.event_type == "llm_response" or event.event_type == "presentation_transform"
        ]
        worker_summaries = [
            event.payload for event in self.events if event.event_type == "worker_draft"
        ]
        coverage = next(
            (
                event.payload
                for event in reversed(self.events)
                if event.event_type == "contract_coverage"
            ),
            {},
        )
        request_coverage = next(
            (
                event.payload
                for event in reversed(self.events)
                if event.event_type == "request_coverage"
            ),
            {},
        )
        return {
            "run_id": self.run_id,
            "event_count": len(self.events),
            "events": [event.event_type for event in self.events],
            "status": (end.payload if end else {}).get("status", "running"),
            "latency_ms": (end.payload if end else {}).get("latency_ms"),
            "llm_calls": sum(event.event_type == "llm_request" for event in self.events)
            + sum(event.event_type == "presentation_transform" for event in self.events),
            "tool_calls": sum(event.event_type == "tool_call" for event in self.events),
            "tokens": sum(int(item.get("total_tokens", 0)) for item in usage) or None,
            "worker_infrastructure_retry_count": sum(
                int(item.get("infrastructure_retry_count", 0))
                for item in worker_summaries
            ),
            "workers_recovered_after_infra_retry": sum(
                int(item.get("infrastructure_retry_count", 0)) > 0
                and item.get("worker_status") == "success"
                for item in worker_summaries
            ),
            "workers_failed_after_infra_retry": sum(
                int(item.get("infrastructure_retry_count", 0)) > 0
                and item.get("worker_status") == "provider_error"
                for item in worker_summaries
            ),
            "protocol_recovery_count": sum(
                event.event_type == "worker_protocol_recovery_start"
                for event in self.events
            ),
            "protocol_recovery_success_count": sum(
                event.event_type == "worker_protocol_recovery_result"
                and bool(event.payload.get("success"))
                for event in self.events
            ),
            "required_deliverable_ids": coverage.get("required_deliverable_ids", []),
            "covered_deliverable_ids": coverage.get("covered_deliverable_ids", []),
            "missing_required_deliverables": coverage.get(
                "missing_required_deliverables", []
            ),
            "contract_complete": not bool(
                coverage.get("missing_required_deliverables", [])
            ),
            "required_request_items": request_coverage.get(
                "required_request_items", []
            ),
            "covered_request_items": request_coverage.get(
                "covered_request_items", []
            ),
            "missing_request_items": request_coverage.get("missing_request_items", []),
            "user_request_complete": bool(
                request_coverage.get("user_request_complete", False)
            ),
        }
