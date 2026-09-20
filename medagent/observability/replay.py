from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_trace(run_dir: str | Path) -> list[dict[str, Any]]:
    path = Path(run_dir)
    if path.is_file():
        trace_file = path
    else:
        trace_file = path / "trace.jsonl"
    if not trace_file.is_file():
        raise FileNotFoundError(f"trace not found: {trace_file}")
    return [
        json.loads(line)
        for line in trace_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def trace_summary(run_dir: str | Path) -> dict[str, Any]:
    events = read_trace(run_dir)
    final = next((item for item in reversed(events) if item["event"] == "run_end"), None)
    return {
        "run_id": events[0]["run_id"] if events else None,
        "status": (final or {}).get("payload", {}).get("status", "incomplete"),
        "event_count": len(events),
        "events": [item["event"] for item in events],
        "latency_ms": (final or {}).get("payload", {}).get("latency_ms"),
    }


def replay(run_dir: str | Path) -> dict[str, Any]:
    events = read_trace(run_dir)
    final = next((item for item in reversed(events) if item["event"] == "final_answer"), None)
    return {
        "summary": trace_summary(run_dir),
        "final_answer": (final or {}).get("payload", {}).get("answer", ""),
    }
