from __future__ import annotations

import json
from typing import Any

from medagent.agents.base import AgentDefinition, RequestItemAnswer, WorkerResult
from medagent.context.contract import AnswerContract
from medagent.context.evidence_ledger import EvidenceLedger
from medagent.context.request_spec import RequestSpec
from medagent.llm.client import LLMClient
from medagent.llm.generation import GenerationPolicy, run_with_length_recovery
from medagent.observability.tracer import TraceRecorder
from medagent.planning.complexity import ResponseProfile, TaskComplexityProfile
from medagent.planning.models import Subtask
from medagent.skills.loader import ProceduralSkill
from medagent.tools.registry import ToolRegistry


def parse_worker_response(
    content: str, assigned_request_item_ids: list[str]
) -> list[RequestItemAnswer]:
    """Parse validated per-item answers, with single-item legacy text normalization."""

    clean = content.strip()
    if not clean:
        return []
    try:
        parsed: Any = json.loads(clean)
    except json.JSONDecodeError:
        if len(assigned_request_item_ids) == 1:
            return [RequestItemAnswer(assigned_request_item_ids[0], clean)]
        return []
    if not isinstance(parsed, dict) or not isinstance(parsed.get("answers"), list):
        return []
    assigned = set(assigned_request_item_ids)
    answers: list[RequestItemAnswer] = []
    seen: set[str] = set()
    for value in parsed["answers"]:
        if not isinstance(value, dict):
            continue
        request_item_id = str(value.get("request_item_id") or "").strip()
        answer = str(value.get("answer") or "").strip()
        if request_item_id not in assigned or request_item_id in seen or not answer:
            continue
        item = RequestItemAnswer(request_item_id, answer)
        item.validate()
        answers.append(item)
        seen.add(request_item_id)
    return answers


class AgentLoop:
    def __init__(
        self,
        llm: LLMClient,
        tools: ToolRegistry,
        max_tool_calls: int = 2,
        *,
        max_tokens: int = 8192,
        max_length_recoveries: int = 1,
        max_infrastructure_retries: int = 1,
    ) -> None:
        self.llm = llm
        self.tools = tools
        self.max_tool_calls = max_tool_calls
        self.generation_policy = GenerationPolicy(max_tokens, max_length_recoveries)
        self.max_infrastructure_retries = max_infrastructure_retries

    async def execute(
        self,
        agent: AgentDefinition,
        subtask: Subtask,
        request_spec: RequestSpec,
        contract: AnswerContract,
        ledger: EvidenceLedger,
        current_context: dict[str, str],
        memory_context: list[dict[str, str]],
        skill: ProceduralSkill,
        trace: TraceRecorder,
        complexity: TaskComplexityProfile,
        response_profile: ResponseProfile,
    ) -> WorkerResult:
        messages: list[dict[str, object]] = [
            {
                "role": "system",
                "content": (
                    f"Role: {agent.role}. Scope: {agent.scope} Safety: {agent.safety_boundary} "
                    "Return a concise worker draft; do not reveal hidden reasoning.\n\n"
                    "For the final response, return one JSON object with an answers array. "
                    "Each entry must have request_item_id and a non-empty answer. Include only "
                    "assigned request_item_ids, and answer each assigned item separately. "
                    "Use tools only when they materially help satisfy the requested "
                    "deliverables. Do not expand into unrequested clinical sections. "
                    f"Response objective: {response_profile.objective}\n\n"
                    f"Public procedural skill ({skill.name}):\n{skill.instructions}"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "bounded_session_context": memory_context,
                        "current_context": current_context,
                        "subtask": subtask.to_dict(),
                        "request_spec": request_spec.to_dict(),
                        "contract": contract.to_dict(),
                        "complexity_profile": complexity.to_dict(),
                        "response_profile": response_profile.to_dict(),
                        "patient_facts": ledger.to_dict(),
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        calls = 0
        length_recovery_count = 0
        infrastructure_retry_count = 0
        provider_attempt_count = 0
        sent_evidence_ids: set[str] = set()
        while True:
            schemas = self.tools.schemas_for(agent.agent_id)
            try:
                generation = await run_with_length_recovery(
                    self.llm,
                    policy=self.generation_policy,
                    stage="worker",
                    agent=agent.agent_id,
                    messages=messages,
                    tools=schemas,
                    purpose=f"worker:{subtask.subtask_id}",
                    is_complete=lambda response: bool(
                        response.tool_calls
                        or parse_worker_response(
                            response.content, subtask.request_item_ids
                        )
                    ),
                    trace=trace,
                    max_infrastructure_retries=self.max_infrastructure_retries,
                    worker_id=agent.agent_id,
                    subtask_id=subtask.subtask_id,
                    provider_attempt_index_offset=provider_attempt_count,
                )
            except Exception as error:
                infrastructure_retry_count += int(
                    getattr(error, "infrastructure_retry_count", 0)
                )
                provider_attempt_count += int(getattr(error, "provider_attempt_count", 1))
                result = WorkerResult(
                    worker=agent.agent_id,
                    subtask_id=subtask.subtask_id,
                    answer="",
                    success=False,
                    tool_calls=calls,
                    generation_status="provider_error",
                    length_recovery_count=length_recovery_count,
                    failure_reason="provider_error",
                    infrastructure_retry_count=infrastructure_retry_count,
                    provider_attempt_count=provider_attempt_count,
                    worker_status="provider_error",
                )
                trace.record(
                    "worker_draft",
                    result.to_dict(),
                    stage="worker",
                    agent=agent.agent_id,
                )
                return result
            infrastructure_retry_count += generation.infrastructure_retry_count
            provider_attempt_count += generation.provider_attempt_count
            response = generation.response
            response_id = generation.response_id
            length_recovery_count += generation.length_recovery_count
            if not response.tool_calls:
                generation_status = generation.generation_status
                if length_recovery_count and generation_status != "length_exhausted":
                    generation_status = "completed_after_length_recovery"
                request_item_answers = parse_worker_response(
                    response.content, subtask.request_item_ids
                )
                success = bool(request_item_answers) and generation_status != "length_exhausted"
                failure_reason = None
                if generation_status == "length_exhausted":
                    failure_reason = "generation_length_exhausted"
                elif not success:
                    failure_reason = "invalid_or_empty_worker_response"
                normalized_answer = "\n\n".join(
                    item.answer for item in request_item_answers
                )
                result = WorkerResult(
                    worker=agent.agent_id,
                    subtask_id=subtask.subtask_id,
                    answer=normalized_answer,
                    success=success,
                    tool_calls=calls,
                    generation_status=generation_status,
                    length_recovery_count=length_recovery_count,
                    failure_reason=failure_reason,
                    infrastructure_retry_count=infrastructure_retry_count,
                    provider_attempt_count=provider_attempt_count,
                    worker_status="success" if success else "generation_error",
                    request_item_answers=request_item_answers,
                )
                trace.record(
                    "worker_draft",
                    result.to_dict(),
                    stage="worker",
                    agent=agent.agent_id,
                    parent_event_id=response_id,
                )
                return result
            messages.append(
                {
                    "role": "assistant",
                    "content": response.content,
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": json.dumps(call.arguments, ensure_ascii=False),
                            },
                        }
                        for call in response.tool_calls
                    ],
                }
            )
            for call in response.tool_calls:
                if calls >= self.max_tool_calls:
                    output: object = {"error": "tool call budget exhausted"}
                else:
                    call_id = trace.record(
                        "tool_call",
                        {"name": call.name, "arguments": call.arguments},
                        stage="tool",
                        agent=agent.agent_id,
                        parent_event_id=response_id,
                    )
                    result_parent = call_id
                    try:
                        output = await self.tools.execute(agent.agent_id, call.name, call.arguments)
                    except (LookupError, PermissionError, RuntimeError, TypeError) as error:
                        output = {"error": str(error)}
                    retrieval_trace = None
                    if isinstance(output, dict) and "_trace_retrieval" in output:
                        output = dict(output)
                        retrieval_trace = output.pop("_trace_retrieval")
                    if isinstance(retrieval_trace, dict):
                        query_id = trace.record(
                            "retrieval_query",
                            {
                                "tool": call.name,
                                "query": retrieval_trace.get("query"),
                                "collection": retrieval_trace.get("collection"),
                                "routing_reason": retrieval_trace.get("routing_reason"),
                                "top_k": retrieval_trace.get("top_k"),
                            },
                            stage="retrieval",
                            agent=agent.agent_id,
                            parent_event_id=call_id,
                        )
                        result_parent = trace.record(
                            "retrieval_result",
                            {
                                "tool": call.name,
                                "raw_candidates": retrieval_trace.get("raw_candidates", []),
                                "admission": retrieval_trace.get("admission", []),
                                "compact_evidence": retrieval_trace.get("compact_evidence", []),
                            },
                            stage="retrieval",
                            agent=agent.agent_id,
                            parent_event_id=query_id,
                        )
                    if isinstance(output, dict) and isinstance(output.get("admitted"), list):
                        deduplicated = []
                        for evidence in output["admitted"]:
                            if not isinstance(evidence, dict):
                                continue
                            evidence_id = str(evidence.get("evidence_id") or "")
                            if evidence_id and evidence_id in sent_evidence_ids:
                                continue
                            if evidence_id:
                                sent_evidence_ids.add(evidence_id)
                            deduplicated.append(evidence)
                        output = {
                            **output,
                            "evidence_status": (
                                "relevant_evidence_admitted"
                                if deduplicated
                                else "no_new_relevant_evidence"
                            ),
                            "admitted": deduplicated,
                        }
                    trace.record(
                        "tool_result",
                        {"name": call.name, "result": output},
                        stage="tool",
                        agent=agent.agent_id,
                        parent_event_id=result_parent,
                    )
                    calls += 1
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "name": call.name,
                        "content": json.dumps(output, ensure_ascii=False, default=str),
                    }
                )
