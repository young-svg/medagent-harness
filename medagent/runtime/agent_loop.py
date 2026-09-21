from __future__ import annotations

import json

from medagent.agents.base import AgentDefinition, WorkerResult
from medagent.context.contract import AnswerContract
from medagent.context.evidence_ledger import EvidenceLedger
from medagent.llm.client import LLMClient
from medagent.observability.tracer import TraceRecorder
from medagent.planning.models import Subtask
from medagent.tools.registry import ToolRegistry


class AgentLoop:
    def __init__(self, llm: LLMClient, tools: ToolRegistry, max_tool_calls: int = 2) -> None:
        self.llm = llm
        self.tools = tools
        self.max_tool_calls = max_tool_calls

    async def execute(
        self,
        agent: AgentDefinition,
        subtask: Subtask,
        contract: AnswerContract,
        ledger: EvidenceLedger,
        compact_evidence: list[dict[str, object]],
        trace: TraceRecorder,
    ) -> WorkerResult:
        messages: list[dict[str, object]] = [
            {
                "role": "system",
                "content": (
                    f"Role: {agent.role}. Scope: {agent.scope} Safety: {agent.safety_boundary} "
                    "Return a concise worker draft; do not reveal hidden reasoning."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "subtask": subtask.to_dict(),
                        "contract": contract.to_dict(),
                        "patient_facts": ledger.to_dict(),
                        "admitted_evidence": compact_evidence,
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        calls = 0
        while True:
            schemas = self.tools.schemas_for(agent.agent_id)
            request_id = trace.record(
                "llm_request",
                {
                    "subtask_id": subtask.subtask_id,
                    "visible_tools": [item["function"]["name"] for item in schemas],
                },
                stage="worker",
                agent=agent.agent_id,
            )
            response = await self.llm.complete(messages, tools=schemas)
            response_id = trace.record(
                "llm_response",
                {
                    "subtask_id": subtask.subtask_id,
                    "usage": response.usage,
                    "tool_call_count": len(response.tool_calls),
                },
                stage="worker",
                agent=agent.agent_id,
                parent_event_id=request_id,
            )
            if not response.tool_calls:
                result = WorkerResult(
                    agent.agent_id,
                    subtask.subtask_id,
                    response.content,
                    bool(response.content.strip()),
                    calls,
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
                    "tool_calls": [call.id for call in response.tool_calls],
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
                    retrieval_parent = call_id
                    if call.name in {
                        "clinical_guideline",
                        "disease_code",
                        "recommend_lifestyle",
                        "deep_research",
                        "search_knowledge",
                    }:
                        retrieval_parent = trace.record(
                            "retrieval_query",
                            {"tool": call.name, "arguments": call.arguments},
                            stage="retrieval",
                            agent=agent.agent_id,
                            parent_event_id=call_id,
                        )
                    try:
                        output = await self.tools.execute(agent.agent_id, call.name, call.arguments)
                    except (LookupError, PermissionError, RuntimeError, TypeError) as error:
                        output = {"error": str(error)}
                    trace_output = output
                    if isinstance(output, dict) and "_trace_bundle" in output:
                        output = dict(output)
                        trace_output = output.pop("_trace_bundle")
                    if retrieval_parent != call_id:
                        retrieval_parent = trace.record(
                            "retrieval_result",
                            {"tool": call.name, "evidence_bundle": trace_output},
                            stage="retrieval",
                            agent=agent.agent_id,
                            parent_event_id=retrieval_parent,
                        )
                    trace.record(
                        "tool_result",
                        {"name": call.name, "result": output},
                        stage="tool",
                        agent=agent.agent_id,
                        parent_event_id=retrieval_parent,
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
