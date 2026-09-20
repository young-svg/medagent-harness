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
        self._events: list[TraceEvent] = []

    def record(
        self, event: str, payload: dict[str, Any] | None = None, parent_id: str | None = None
    ) -> None:
        resolved_parent = None if event == "run_start" else (parent_id or self.run_id)
        item = TraceEvent(event, self.run_id, payload or {}, parent_id=resolved_parent)
        self._events.append(item)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(item.to_dict(), ensure_ascii=False) + "\n")

    def finish(self, status: str = "completed") -> None:
        self.record(
            "run_end",
            {"status": status, "latency_ms": round((perf_counter() - self.started) * 1000, 2)},
        )

    def summary(self) -> dict[str, Any]:
        metrics = (
            self._events[-1].payload if self._events and self._events[-1].event == "run_end" else {}
        )
        return {
            "run_id": self.run_id,
            "event_count": len(self._events),
            "events": [item.event for item in self._events],
            "status": metrics.get("status", "running"),
            "latency_ms": metrics.get("latency_ms"),
            "llm_calls": sum(item.event == "llm_request" for item in self._events),
            "tool_calls": sum(item.event == "tool_call" for item in self._events),
            "tokens": 0,
        }
