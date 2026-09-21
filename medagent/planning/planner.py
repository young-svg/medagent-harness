from __future__ import annotations

import json
from typing import Any

from medagent.context.contract import AnswerContract
from medagent.context.evidence_ledger import EvidenceLedger
from medagent.llm.client import LLMClient
from medagent.observability.tracer import TraceRecorder
from medagent.planning.models import VALID_WORKERS, Plan, Subtask


def _fallback_worker(contract: AnswerContract) -> str:
    if any("TREATMENT" in item or "MANAGEMENT" in item for item in contract.requested_deliverables):
        return "consultation_agent"
    return "diagnostic_agent"


class Planner:
    """Centralized planner with strict structured parsing and deterministic fallback."""

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm

    @staticmethod
    def parse(raw: Any, fallback_worker: str) -> Plan:
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            subtasks = []
            for index, item in enumerate(parsed["subtasks"], 1):
                agent = str(item.get("assigned_agent") or fallback_worker)
                if agent not in VALID_WORKERS:
                    agent = fallback_worker
                subtasks.append(
                    Subtask(
                        str(item.get("subtask_id") or f"task-{index}"),
                        str(item["description"]),
                        agent,
                    )
                )
            plan = Plan(subtasks)
            plan.validate()
            return plan
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            return Plan(
                [Subtask("task-1", "Address the requested deliverables.", fallback_worker)],
                f"structured_parse_fallback:{type(error).__name__}",
            )

    async def plan(
        self,
        question: str,
        contract: AnswerContract,
        ledger: EvidenceLedger,
        trace: TraceRecorder | None = None,
    ) -> Plan:
        fallback_worker = _fallback_worker(contract)
        if self.llm is None:
            return self.parse({}, fallback_worker)
        prompt = (
            "You are the public MedAgent centralized planner. Return JSON with a subtasks array. "
            "Each item needs subtask_id, a concise description, and assigned_agent chosen only "
            "from diagnostic_agent, consultation_agent, research_agent. Do not include reasoning.\n"
            f"Question: {question}\n"
            f"Contract: {json.dumps(contract.to_dict(), ensure_ascii=False)}\n"
            f"Ledger: {json.dumps(ledger.to_dict(), ensure_ascii=False)}"
        )
        request_id = (
            trace.record("llm_request", {"purpose": "planner"}, stage="planning", agent="planner")
            if trace
            else None
        )
        try:
            response = await self.llm.complete(
                [{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
            )
            if trace:
                trace.record(
                    "llm_response",
                    {"purpose": "planner", "usage": response.usage},
                    stage="planning",
                    agent="planner",
                    parent_event_id=request_id,
                )
            return self.parse(response.content, fallback_worker)
        except Exception as error:
            return Plan(
                [Subtask("task-1", question, fallback_worker)],
                f"planner_request_fallback:{type(error).__name__}",
            )
