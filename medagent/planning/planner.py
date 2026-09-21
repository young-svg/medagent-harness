from __future__ import annotations

import json
from typing import Any

from medagent.context.contract import AnswerContract
from medagent.context.evidence_ledger import EvidenceLedger
from medagent.llm.client import LLMClient
from medagent.observability.llm import complete_with_trace
from medagent.observability.tracer import TraceRecorder
from medagent.planning.models import VALID_WORKERS, Plan, Subtask


def _fallback_worker(contract: AnswerContract) -> str:
    if any("TREATMENT" in item or "MANAGEMENT" in item for item in contract.requested_deliverables):
        return "consultation_agent"
    return "diagnostic_agent"


class Planner:
    """Centralized planner with strict structured parsing and deterministic fallback."""

    def __init__(
        self,
        llm: LLMClient | None = None,
        *,
        max_tokens: int = 8192,
        max_length_recoveries: int = 1,
    ) -> None:
        if max_tokens <= 0:
            raise ValueError("planner max_tokens must be positive")
        if max_length_recoveries not in {0, 1}:
            raise ValueError("planner max_length_recoveries must be 0 or 1")
        self.llm = llm
        self.max_tokens = max_tokens
        self.max_length_recoveries = max_length_recoveries

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
                planner_parse_status="fallback",
            )

    async def plan(
        self,
        question: str,
        contract: AnswerContract,
        ledger: EvidenceLedger,
        trace: TraceRecorder | None = None,
        current_context: dict[str, str] | None = None,
        memory_context: list[dict[str, str]] | None = None,
    ) -> Plan:
        fallback_worker = _fallback_worker(contract)
        if self.llm is None:
            return self.parse({}, fallback_worker)
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "You are the public MedAgent centralized planner. Return JSON with a "
                    "subtasks array. Each item needs subtask_id, a concise description, and "
                    "assigned_agent chosen only "
                    "from diagnostic_agent, consultation_agent, research_agent. "
                    "Do not include reasoning. Current request data overrides session history."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Bounded session context (background only): "
                    f"{json.dumps(memory_context or [], ensure_ascii=False)}\n"
                    "Current request (authoritative): "
                    f"{json.dumps(current_context or {'question': question}, ensure_ascii=False)}\n"
                    f"Question: {question}\n"
                    f"Contract: {json.dumps(contract.to_dict(), ensure_ascii=False)}\n"
                    f"Ledger: {json.dumps(ledger.to_dict(), ensure_ascii=False)}"
                ),
            },
        ]
        async def request(attempt_index: int, recovery_type: str | None):
            if trace:
                response, _ = await complete_with_trace(
                    self.llm,
                    trace,
                    stage="planning",
                    agent="planner",
                    messages=messages,
                    response_format={"type": "json_object"},
                    purpose="planner",
                    max_tokens=self.max_tokens,
                    planner_max_tokens=self.max_tokens,
                    attempt_index=attempt_index,
                    recovery_type=recovery_type,
                )
                return response
            return await self.llm.complete(
                messages,
                response_format={"type": "json_object"},
                max_tokens=self.max_tokens,
            )

        try:
            response = await request(1, None)
            plan = self.parse(response.content, fallback_worker)
            if plan.fallback_reason is None or response.finish_reason != "length":
                return plan
            if self.max_length_recoveries == 0:
                plan.planner_generation_status = "length_exhausted"
                return plan

            response = await request(2, "structured_output_completion_recovery")
            plan = self.parse(response.content, fallback_worker)
            plan.planner_length_recovery_count = 1
            if plan.fallback_reason is None:
                plan.planner_generation_status = "completed_after_length_recovery"
            else:
                plan.planner_generation_status = "length_exhausted"
            return plan
        except Exception as error:
            return Plan(
                [Subtask("task-1", question, fallback_worker)],
                f"planner_request_fallback:{type(error).__name__}",
                planner_parse_status="fallback",
                planner_generation_status=None,
            )
