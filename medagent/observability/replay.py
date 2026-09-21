from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_trace(run_dir: str | Path) -> list[dict[str, Any]]:
    path = Path(run_dir)
    trace_file = path if path.is_file() else path / "trace.jsonl"
    if not trace_file.is_file():
        raise FileNotFoundError(f"trace not found: {trace_file}")
    return [
        json.loads(line)
        for line in trace_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def trace_summary(run_dir: str | Path) -> dict[str, Any]:
    events = read_trace(run_dir)
    final = next((item for item in reversed(events) if item["event_type"] == "run_end"), None)
    payload = (final or {}).get("payload") or {}
    coverage = next(
        (
            item.get("payload") or {}
            for item in reversed(events)
            if item["event_type"] == "contract_coverage"
        ),
        {},
    )
    return {
        "run_id": events[0].get("run_id") if events else None,
        "status": payload.get("status", "incomplete"),
        "event_count": len(events),
        "events": [item["event_type"] for item in events],
        "latency_ms": payload.get("latency_ms"),
        "missing_required_deliverables": coverage.get(
            "missing_required_deliverables", []
        ),
    }


def replay(run_dir: str | Path) -> dict[str, Any]:
    events = read_trace(run_dir)
    final = next((item for item in reversed(events) if item["event_type"] == "final_answer"), None)
    return {
        "mode": "replay",
        "summary": trace_summary(run_dir),
        "final_answer": ((final or {}).get("payload") or {}).get("answer", ""),
    }
