from __future__ import annotations

import json
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from medagent.agents.base import AgentDefinition, RequestItemAnswer, WorkerResult
from medagent.context.contract import AnswerContract
from medagent.context.evidence_ledger import EvidenceLedger
from medagent.context.request_spec import RequestSpec
from medagent.llm.client import LLMClient
from medagent.llm.generation import GenerationPolicy, run_with_length_recovery
from medagent.observability.llm import complete_with_trace
from medagent.observability.tracer import TraceRecorder
from medagent.planning.complexity import ResponseProfile, TaskComplexityProfile
from medagent.planning.models import Subtask
from medagent.skills.loader import ProceduralSkill
from medagent.tools.registry import ToolRegistry

MAX_PROTOCOL_RECOVERY_PER_WORKER = 1

_PROTOCOL_RECOVERY_SYSTEM = """You are a serialization repair step.

The previous worker already performed the clinical task, but its response failed the
WorkerResponse JSON protocol.

Do NOT redo the clinical analysis.
Do NOT add, remove, reinterpret, or improve clinical claims.
Do NOT answer any new request.
Do NOT call tools.
Do NOT include explanations, markdown, commentary, or multiple JSON objects.

Re-serialize only the substantive answers already present in the previous response into
exactly one valid WorkerResponse JSON object. Only include allowed request_item_ids, and
return JSON only."""

_WORKER_RESPONSE_SCHEMA: dict[str, object] = {
    "answers": [
        {
            "request_item_id": "allowed request item ID",
            "answer": "non-empty string",
        }
    ]
}


@dataclass(frozen=True, slots=True)
class WorkerResponseParseResult:
    answers: list[RequestItemAnswer]
    parse_status: str
    recovery_method: str | None = None

    def __bool__(self) -> bool:
        return bool(self.answers)


@dataclass(frozen=True, slots=True)
class ProtocolRecoveryResult:
    parsed: WorkerResponseParseResult
    attempted: bool
    success: bool
    provider_attempt_count: int
    response_id: str | None = None


def _validated_answers(
    parsed: Any,
    assigned_request_item_ids: list[str],
    *,
    strict_schema: bool,
) -> list[RequestItemAnswer]:
    if not isinstance(parsed, dict) or not isinstance(parsed.get("answers"), list):
        return []
    if strict_schema and set(parsed) != {"answers"}:
        return []
    raw_answers = parsed["answers"]
    if not raw_answers:
        return []
    assigned = set(assigned_request_item_ids)
    answers: list[RequestItemAnswer] = []
    seen: set[str] = set()
    for value in raw_answers:
        if not isinstance(value, dict):
            if strict_schema:
                return []
            continue
        if strict_schema and set(value) != {"request_item_id", "answer"}:
            return []
        raw_request_item_id = value.get("request_item_id")
        raw_answer = value.get("answer")
        if strict_schema and not isinstance(raw_request_item_id, str):
            return []
        if strict_schema and not isinstance(raw_answer, str):
            return []
        request_item_id = str(raw_request_item_id or "").strip()
        answer = str(raw_answer or "").strip()
        if request_item_id not in assigned or request_item_id in seen or not answer:
            if strict_schema:
                return []
            continue
        item = RequestItemAnswer(request_item_id, answer)
        item.validate()
        answers.append(item)
        seen.add(request_item_id)
    return answers


def _is_redundant_closing_tail(value: str) -> bool:
    return bool(value) and any(character in "]}" for character in value) and all(
        character.isspace() or character in "]}" for character in value
    )


def parse_worker_response(
    content: str, assigned_request_item_ids: list[str]
) -> WorkerResponseParseResult:
    """Parse validated per-item answers, with single-item legacy text normalization."""

    clean = content.strip()
    if not clean:
        return WorkerResponseParseResult([], "invalid")
    try:
        parsed: Any = json.loads(clean)
    except json.JSONDecodeError:
        try:
            recovered, end = json.JSONDecoder().raw_decode(clean)
        except json.JSONDecodeError:
            recovered = None
            end = 0
        trailing = clean[end:]
        recovered_answers = _validated_answers(
            recovered,
            assigned_request_item_ids,
            strict_schema=True,
        )
        if recovered_answers and _is_redundant_closing_tail(trailing):
            return WorkerResponseParseResult(
                recovered_answers,
                "recovered_json",
                "trailing_closing_delimiters",
            )
        if len(assigned_request_item_ids) == 1 and not clean.startswith(("{", "[")):
            return WorkerResponseParseResult(
                [RequestItemAnswer(assigned_request_item_ids[0], clean)],
                "legacy_text",
            )
        return WorkerResponseParseResult([], "invalid")
    answers = _validated_answers(
        parsed,
        assigned_request_item_ids,
        strict_schema=False,
    )
    return WorkerResponseParseResult(
        answers,
        "direct_json" if answers else "invalid",
    )


def _protocol_recovery_messages(
    malformed_response: str, assigned_request_item_ids: list[str]
) -> list[dict[str, object]]:
    return [
        {"role": "system", "content": _PROTOCOL_RECOVERY_SYSTEM},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "malformed_worker_response": malformed_response,
                    "allowed_request_item_ids": assigned_request_item_ids,
                    "required_schema": _WORKER_RESPONSE_SCHEMA,
                },
                ensure_ascii=False,
            ),
        },
    ]


def _recovery_usage(usage: dict[str, Any]) -> dict[str, int]:
    completion_details = usage.get("completion_tokens_details") or {}
    reasoning_tokens = usage.get("reasoning_tokens")
    if reasoning_tokens is None:
        reasoning_tokens = completion_details.get("reasoning_tokens", 0)
    return {
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "reasoning_tokens": int(reasoning_tokens or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
    }


def _parse_protocol_recovery_response(
    content: str, assigned_request_item_ids: list[str]
) -> WorkerResponseParseResult:
    parsed = parse_worker_response(content, assigned_request_item_ids)
    if parsed.parse_status != "direct_json":
        return WorkerResponseParseResult([], "invalid")
    try:
        value = json.loads(content.strip())
    except json.JSONDecodeError:
        return WorkerResponseParseResult([], "invalid")
    answers = _validated_answers(value, assigned_request_item_ids, strict_schema=True)
    return WorkerResponseParseResult(
        answers,
        "direct_json" if answers else "invalid",
    )


class AgentLoop:
    def __init__(
        self,
        llm: LLMClient,
        tools: ToolRegistry,
        max_tool_calls: int = 2,
        *,
        max_tokens: int = 8192,
        max_length_recoveries: int = 1,
        max_infrastructure_retries: int = 2,
        infrastructure_retry_base_delay_seconds: float = 1.0,
    ) -> None:
        self.llm = llm
        self.tools = tools
        self.max_tool_calls = max_tool_calls
        self.generation_policy = GenerationPolicy(max_tokens, max_length_recoveries)
        self.max_infrastructure_retries = max_infrastructure_retries
        self.infrastructure_retry_base_delay_seconds = (
            infrastructure_retry_base_delay_seconds
        )

    async def _attempt_protocol_recovery(
        self,
        *,
        agent: AgentDefinition,
        subtask: Subtask,
        malformed_response: str,
        original_parse_status: str,
        original_finish_reason: str | None,
        trace: TraceRecorder,
        parent_event_id: str | None,
    ) -> ProtocolRecoveryResult:
        attempt = 1
        start_id = trace.record(
            "worker_protocol_recovery_start",
            {
                "worker": agent.agent_id,
                "subtask_id": subtask.subtask_id,
                "original_parse_status": original_parse_status,
                "original_finish_reason": original_finish_reason,
                "attempt": attempt,
                "max_attempts": MAX_PROTOCOL_RECOVERY_PER_WORKER,
            },
            stage="worker_protocol_recovery",
            agent=agent.agent_id,
            parent_event_id=parent_event_id,
        )
        started = perf_counter()
        try:
            response, response_id = await complete_with_trace(
                self.llm,
                trace,
                stage="worker_protocol_recovery",
                agent=agent.agent_id,
                messages=_protocol_recovery_messages(
                    malformed_response, subtask.request_item_ids
                ),
                tools=None,
                response_format=None,
                purpose=f"worker_protocol_recovery:{subtask.subtask_id}",
                parent_event_id=start_id,
                max_tokens=self.generation_policy.max_tokens,
                temperature=0.0,
                attempt_index=attempt,
                recovery_type="worker_protocol_recovery",
            )
        except Exception as error:
            trace.record(
                "worker_protocol_recovery_result",
                {
                    "worker": agent.agent_id,
                    "subtask_id": subtask.subtask_id,
                    "attempt": attempt,
                    "provider_outcome": "error",
                    "provider_error_type": type(error).__name__,
                    "recovery_finish_reason": None,
                    "recovery_parse_status": "invalid",
                    "success": False,
                    "usage": _recovery_usage({}),
                    "latency_ms": round((perf_counter() - started) * 1000, 2),
                },
                stage="worker_protocol_recovery",
                agent=agent.agent_id,
                parent_event_id=start_id,
            )
            return ProtocolRecoveryResult(
                WorkerResponseParseResult([], "invalid"), True, False, 1
            )

        parsed = (
            _parse_protocol_recovery_response(
                response.content, subtask.request_item_ids
            )
            if not response.tool_calls and response.content.strip()
            else WorkerResponseParseResult([], "invalid")
        )
        success = (
            response.finish_reason == "stop"
            and not response.tool_calls
            and bool(response.content.strip())
            and bool(parsed)
        )
        trace.record(
            "worker_protocol_recovery_result",
            {
                "worker": agent.agent_id,
                "subtask_id": subtask.subtask_id,
                "attempt": attempt,
                "provider_outcome": "success",
                "provider_error_type": None,
                "recovery_finish_reason": response.finish_reason,
                "recovery_parse_status": parsed.parse_status,
                "success": success,
                "usage": _recovery_usage(response.usage),
                "latency_ms": round((perf_counter() - started) * 1000, 2),
            },
            stage="worker_protocol_recovery",
            agent=agent.agent_id,
            parent_event_id=response_id,
        )
        return ProtocolRecoveryResult(parsed, True, success, 1, response_id)

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
        protocol_recovery_count = 0
        protocol_recovery_success_count = 0
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
                    infrastructure_retry_base_delay_seconds=(
                        self.infrastructure_retry_base_delay_seconds
                    ),
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
                    protocol_recovery_count=protocol_recovery_count,
                    protocol_recovery_success_count=protocol_recovery_success_count,
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
                parsed_response = parse_worker_response(
                    response.content, subtask.request_item_ids
                )
                final_response_id = response_id
                protocol_recovery_eligible = (
                    generation_status != "length_exhausted"
                    and response.finish_reason == "stop"
                    and bool(response.content.strip())
                    and not parsed_response
                    and protocol_recovery_count < MAX_PROTOCOL_RECOVERY_PER_WORKER
                )
                if protocol_recovery_eligible:
                    recovery = await self._attempt_protocol_recovery(
                        agent=agent,
                        subtask=subtask,
                        malformed_response=response.content,
                        original_parse_status=parsed_response.parse_status,
                        original_finish_reason=response.finish_reason,
                        trace=trace,
                        parent_event_id=response_id,
                    )
                    protocol_recovery_count += int(recovery.attempted)
                    protocol_recovery_success_count += int(recovery.success)
                    provider_attempt_count += recovery.provider_attempt_count
                    if recovery.success:
                        parsed_response = recovery.parsed
                        final_response_id = recovery.response_id
                        generation_status = "completed_after_protocol_recovery"
                request_item_answers = parsed_response.answers
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
                    protocol_recovery_count=protocol_recovery_count,
                    protocol_recovery_success_count=protocol_recovery_success_count,
                    worker_status="success" if success else "generation_error",
                    request_item_answers=request_item_answers,
                )
                worker_payload = result.to_dict()
                worker_payload["parse_status"] = parsed_response.parse_status
                worker_payload["recovery_method"] = parsed_response.recovery_method
                trace.record(
                    "worker_draft",
                    worker_payload,
                    stage="worker",
                    agent=agent.agent_id,
                    parent_event_id=final_response_id,
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
