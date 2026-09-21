from __future__ import annotations

import json
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from medagent.observability.schema import TraceEvent


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
            payload=payload or {},
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
            if event.event_type == "llm_response"
        ]
        return {
            "run_id": self.run_id,
            "event_count": len(self.events),
            "events": [event.event_type for event in self.events],
            "status": (end.payload if end else {}).get("status", "running"),
            "latency_ms": (end.payload if end else {}).get("latency_ms"),
            "llm_calls": sum(event.event_type == "llm_request" for event in self.events),
            "tool_calls": sum(event.event_type == "tool_call" for event in self.events),
            "tokens": sum(int(item.get("total_tokens", 0)) for item in usage) or None,
        }
