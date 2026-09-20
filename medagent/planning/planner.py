from __future__ import annotations

import json
from typing import Any

from medagent.context.contract import AnswerContract
from medagent.planning.models import VALID_WORKERS, Plan, Subtask


def _worker_for(intent: str) -> str:
    if intent == "management":
        return "consultation_agent"
    if intent in {"research", "evidence_review"}:
        return "research_agent"
    return "diagnostic_agent"


class Planner:
    """Central planner with strict parsing and deterministic fallback."""

    def plan(self, question: str, contract: AnswerContract, raw: Any = None) -> Plan:
        if raw is not None:
            try:
                parsed = json.loads(raw) if isinstance(raw, str) else raw
                subtasks = [
                    Subtask(
                        subtask_id=str(item.get("subtask_id") or f"task-{index}"),
                        description=str(item["description"]),
                        assigned_agent=str(item["assigned_agent"]),
                    )
                    for index, item in enumerate(parsed["subtasks"], 1)
                    if item.get("assigned_agent") in VALID_WORKERS
                ]
                result = Plan(subtasks=subtasks)
                result.validate()
                return result
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                fallback = f"planner_parse_fallback:{type(error).__name__}"
            else:  # pragma: no cover
                fallback = None
        else:
            fallback = None

        workers = [_worker_for(contract.intent)]
        lower = question.lower()
        if any(term in lower for term in ("指南", "文献", "research", "evidence")):
            workers.append("research_agent")
        if any(term in lower for term in ("生活方式", "咨询", "lifestyle")):
            workers.append("consultation_agent")
        workers = list(dict.fromkeys(workers))
        result = Plan(
            subtasks=[
                Subtask(f"task-{index}", question.strip(), worker)
                for index, worker in enumerate(workers, 1)
            ],
            fallback_reason=fallback,
        )
        result.validate()
        return result
