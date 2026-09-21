from __future__ import annotations

import json

from medagent.agents.base import AgentDefinition, WorkerResult
from medagent.context.contract import AnswerContract
from medagent.context.evidence_ledger import EvidenceLedger
from medagent.llm.client import LLMClient
from medagent.observability.llm import complete_with_trace
from medagent.observability.tracer import TraceRecorder
from medagent.planning.models import Subtask
from medagent.skills.loader import ProceduralSkill
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
        current_context: dict[str, str],
        memory_context: list[dict[str, str]],
        skill: ProceduralSkill,
        trace: TraceRecorder,
    ) -> WorkerResult:
        messages: list[dict[str, object]] = [
            {
                "role": "system",
                "content": (
                    f"Role: {agent.role}. Scope: {agent.scope} Safety: {agent.safety_boundary} "
                    "Return a concise worker draft; do not reveal hidden reasoning.\n\n"
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
                        "contract": contract.to_dict(),
                        "patient_facts": ledger.to_dict(),
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        calls = 0
        sent_evidence_ids: set[str] = set()
        while True:
            schemas = self.tools.schemas_for(agent.agent_id)
            response, response_id = await complete_with_trace(
                self.llm,
                trace,
                stage="worker",
                agent=agent.agent_id,
                messages=messages,
                tools=schemas,
                purpose=f"worker:{subtask.subtask_id}",
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
