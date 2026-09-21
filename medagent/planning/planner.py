from __future__ import annotations

import json
from typing import Any

from medagent.context.contract import AnswerContract
from medagent.context.evidence_ledger import EvidenceLedger
from medagent.context.request_spec import RequestItem, RequestSpec
from medagent.llm.client import LLMClient
from medagent.llm.generation import GenerationPolicy, run_with_length_recovery
from medagent.observability.tracer import TraceRecorder
from medagent.planning.complexity import TaskComplexityProfile, preferred_worker
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
                        subtask_id=str(item.get("subtask_id") or f"task-{index}"),
                        description=str(item["description"]),
                        assigned_agent=agent,
                        deliverable_ids=[
                            str(value) for value in item.get("deliverable_ids", [])
                        ],
                        justification=str(item.get("justification") or ""),
                        request_item_ids=[
                            str(value) for value in item.get("request_item_ids", [])
                        ],
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
        complexity: TaskComplexityProfile | None = None,
        request_spec: RequestSpec | None = None,
    ) -> Plan:
        fallback_worker = _fallback_worker(contract)
        if self.llm is None:
            return self.parse({}, fallback_worker)
        complexity_payload = json.dumps(
            complexity.to_dict() if complexity else {}, ensure_ascii=False
        )
        request_spec_payload = json.dumps(
            request_spec.to_dict() if request_spec else {}, ensure_ascii=False
        )
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "You are the public MedAgent centralized planner. Return JSON with a "
                    "subtasks array. Each item needs subtask_id, a concise description, and "
                    "assigned_agent chosen only "
                    "from diagnostic_agent, consultation_agent, research_agent. "
                    "Each item must also include deliverable_ids chosen only from the "
                    "contract requested_deliverables, request_item_ids chosen only from the "
                    "RequestSpec, and a short justification. Every subtask must map at least one "
                    "request item, and every required request item must be mapped. Multiple "
                    "request items may be handled by one worker. Do not create "
                    "diagnosis, differential, testing, management, prognosis, or follow-up "
                    "work unless it is a requested deliverable or a necessary safety dependency. "
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
                    f"RequestSpec: {request_spec_payload}\n"
                    f"Contract: {json.dumps(contract.to_dict(), ensure_ascii=False)}\n"
                    f"Complexity profile: {complexity_payload}\n"
                    f"Ledger: {json.dumps(ledger.to_dict(), ensure_ascii=False)}"
                ),
            },
        ]
        try:
            generation = await run_with_length_recovery(
                self.llm,
                policy=GenerationPolicy(
                    self.max_tokens,
                    self.max_length_recoveries,
                    "structured_output_completion_recovery",
                ),
                stage="planning",
                agent="planner",
                messages=messages,
                purpose="planner",
                is_complete=lambda response: self.parse(
                    response.content, fallback_worker
                ).fallback_reason
                is None,
                trace=trace,
                response_format={"type": "json_object"},
            )
            plan = self.parse(generation.response.content, fallback_worker)
            plan.planner_length_recovery_count = generation.length_recovery_count
            plan.planner_generation_status = generation.generation_status
            return plan
        except Exception as error:
            return Plan(
                [Subtask("task-1", question, fallback_worker)],
                f"planner_request_fallback:{type(error).__name__}",
                planner_parse_status="fallback",
                planner_generation_status="provider_error",
            )


def _mapped_deliverables(subtask: Subtask, contract: AnswerContract) -> list[str]:
    requested = contract.requested_deliverables
    explicit = [item for item in subtask.deliverable_ids if item in requested]
    if explicit:
        return list(dict.fromkeys(explicit))
    if requested == ["COMPREHENSIVE_CASE_ANALYSIS"]:
        return list(requested)
    worker_matches = [
        item
        for item in requested
        if preferred_worker(item) == subtask.assigned_agent
    ]
    if subtask.assigned_agent == "research_agent":
        return []
    return worker_matches


def apply_contract_policy(
    plan: Plan,
    contract: AnswerContract,
    complexity: TaskComplexityProfile,
    request_spec: RequestSpec | None = None,
) -> Plan:
    """Remove planner expansion and attach every retained subtask to contract scope."""

    retained: list[Subtask] = []
    actions: list[str] = []
    for subtask in plan.subtasks:
        mapped = _mapped_deliverables(subtask, contract)
        if subtask.assigned_agent == "research_agent":
            if not complexity.requires_external_evidence:
                if len(plan.subtasks) > 1:
                    actions.append(f"removed_unneeded_research:{subtask.subtask_id}")
                    continue
                mapped = mapped or list(contract.requested_deliverables)
            mapped = mapped or list(contract.requested_deliverables)
        if not mapped:
            actions.append(f"removed_unmapped:{subtask.subtask_id}")
            continue
        retained.append(
            Subtask(
                subtask_id=subtask.subtask_id,
                description=subtask.description,
                assigned_agent=subtask.assigned_agent,
                deliverable_ids=mapped,
                justification=(
                    subtask.justification or "Serves requested contract deliverable(s)."
                ),
                request_item_ids=list(subtask.request_item_ids),
            )
        )

    if complexity.breadth == "focused" and retained:
        primary = contract.requested_deliverables[0]
        selected = next(
            (item for item in retained if primary in item.deliverable_ids), retained[0]
        )
        selected_worker = selected.assigned_agent
        if primary != "COMPREHENSIVE_CASE_ANALYSIS":
            selected_worker = preferred_worker(
                primary, external_evidence=complexity.requires_external_evidence
            )
        retained = [
            Subtask(
                subtask_id=selected.subtask_id,
                description=selected.description,
                assigned_agent=selected_worker,
                deliverable_ids=list(contract.requested_deliverables),
                justification="Focused request handled by the best-matching worker.",
                request_item_ids=(
                    [item.id for item in request_spec.items]
                    if request_spec
                    else list(selected.request_item_ids)
                ),
            )
        ]
        if len(plan.subtasks) > 1:
            actions.append("collapsed_focused_plan")

    if not retained:
        primary = contract.requested_deliverables[0]
        retained = [
            Subtask(
                subtask_id="task-1",
                description="Address only the requested contract deliverable(s).",
                assigned_agent=preferred_worker(
                    primary, external_evidence=complexity.requires_external_evidence
                ),
                deliverable_ids=list(contract.requested_deliverables),
                justification="Deterministic contract-coverage fallback.",
                request_item_ids=(
                    [item.id for item in request_spec.items] if request_spec else []
                ),
            )
        ]
        actions.append("contract_coverage_fallback")

    by_worker: dict[str, Subtask] = {}
    for item in retained:
        existing = by_worker.get(item.assigned_agent)
        if existing is None:
            by_worker[item.assigned_agent] = item
            continue
        deliverable_ids = list(
            dict.fromkeys(existing.deliverable_ids + item.deliverable_ids)
        )
        descriptions = list(
            dict.fromkeys([existing.description.strip(), item.description.strip()])
        )
        by_worker[item.assigned_agent] = Subtask(
            subtask_id=existing.subtask_id,
            description=" ".join(description for description in descriptions if description),
            assigned_agent=item.assigned_agent,
            deliverable_ids=deliverable_ids,
            justification="Merged same-role work serving requested contract deliverables.",
            request_item_ids=list(
                dict.fromkeys(existing.request_item_ids + item.request_item_ids)
            ),
        )
        actions.append(f"merged_same_worker:{item.subtask_id}")

    constrained_subtasks = list(by_worker.values())
    if request_spec:
        constrained_subtasks, request_actions = _apply_request_spec_policy(
            constrained_subtasks, request_spec
        )
        actions.extend(request_actions)

    constrained = Plan(
        constrained_subtasks,
        plan.fallback_reason,
        plan.planner_parse_status,
        plan.planner_generation_status,
        plan.planner_length_recovery_count,
        actions,
    )
    constrained.validate()
    return constrained


def _request_matches_subtask(item: RequestItem, subtask: Subtask) -> bool:
    semantic_type = item.semantic_type or "UNKNOWN"
    if semantic_type in subtask.deliverable_ids:
        return True
    if semantic_type == "UNKNOWN":
        return False
    return preferred_worker(semantic_type) == subtask.assigned_agent


def _apply_request_spec_policy(
    subtasks: list[Subtask], request_spec: RequestSpec
) -> tuple[list[Subtask], list[str]]:
    """Validate planner mappings and deterministically attach any missing request items."""

    valid_ids = {item.id for item in request_spec.items}
    actions: list[str] = []
    for index, subtask in enumerate(subtasks):
        mapped = list(dict.fromkeys(item for item in subtask.request_item_ids if item in valid_ids))
        if not mapped:
            compatible = [
                item.id
                for item in request_spec.items
                if _request_matches_subtask(item, subtask)
            ]
            mapped = compatible or [request_spec.items[index % len(request_spec.items)].id]
            actions.append(f"request_mapping_filled:{subtask.subtask_id}")
        subtask.request_item_ids = mapped

    covered = {item for subtask in subtasks for item in subtask.request_item_ids}
    for request_item in request_spec.items:
        if not request_item.required or request_item.id in covered:
            continue
        compatible = [
            subtask
            for subtask in subtasks
            if _request_matches_subtask(request_item, subtask)
        ]
        target = compatible[0] if compatible else subtasks[0]
        target.request_item_ids.append(request_item.id)
        covered.add(request_item.id)
        actions.append(f"required_request_mapping_filled:{request_item.id}")
    return subtasks, actions
