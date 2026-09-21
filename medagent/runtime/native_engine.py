from __future__ import annotations

import asyncio
import json
from typing import Any

from medagent.agents.base import AgentDefinition, WorkerResult
from medagent.agents.consultation import CONSULTATION_AGENT
from medagent.agents.diagnostic import DIAGNOSTIC_AGENT
from medagent.agents.research import RESEARCH_AGENT
from medagent.context.contract import build_answer_contract
from medagent.context.evidence_ledger import build_evidence_ledger
from medagent.guardrails.contract_checker import Guardrail
from medagent.guardrails.sanitizer import sanitize_answer
from medagent.guardrails.stable_patch import StableDraft, apply_stable_edits
from medagent.llm.client import LLMClient, OpenAICompatibleLLM
from medagent.llm.fakes import DeterministicLLM
from medagent.memory.session import SessionMemory
from medagent.observability.llm import complete_with_trace
from medagent.observability.tracer import TraceRecorder
from medagent.planning.planner import Planner
from medagent.planning.router import Router
from medagent.presentation.adapter import PresentationAdapter
from medagent.retrieval.admission import admit_evidence
from medagent.retrieval.backend import RetrievalBackend
from medagent.retrieval.collection_router import CollectionRouter
from medagent.retrieval.config import RetrievalConfig
from medagent.retrieval.evidence import EvidenceBundle
from medagent.retrieval.factory import build_retrieval_backend
from medagent.retrieval.query_builder import QueryBuilder
from medagent.runtime.agent_loop import AgentLoop
from medagent.runtime.config import RuntimeConfig
from medagent.runtime.engine import EngineExecutionError
from medagent.skills.loader import load_public_skills
from medagent.tools.registry import ToolRegistry

AGENTS: dict[str, AgentDefinition] = {
    item.agent_id: item for item in (DIAGNOSTIC_AGENT, CONSULTATION_AGENT, RESEARCH_AGENT)
}
RETRIEVAL_TOOLS = {
    "clinical_guideline",
    "disease_code",
    "recommend_lifestyle",
    "deep_research",
    "search_knowledge",
}


class NativeMedAgentEngine:
    """Self-contained centralized planner-worker runtime."""

    mode = "native"

    def __init__(
        self,
        config: RuntimeConfig | None = None,
        *,
        llm: LLMClient | None = None,
        retrieval_backend: RetrievalBackend | None = None,
        memory: SessionMemory | None = None,
    ) -> None:
        self.config = config or RuntimeConfig.from_env()
        self.llm = llm or self._build_llm()
        self.retrieval_backend = retrieval_backend or build_retrieval_backend(self.config)
        self.retrieval_mode = (
            self.config.retrieval_mode if retrieval_backend is None else "injected"
        )
        self.memory = memory or SessionMemory()
        self.skills = load_public_skills()
        self._closed = False

    def _build_llm(self) -> LLMClient:
        if self.config.llm_base_url and self.config.llm_api_key:
            return OpenAICompatibleLLM(
                base_url=self.config.llm_base_url,
                api_key=self.config.llm_api_key,
                model=self.config.llm_model,
                temperature=self.config.llm_temperature,
                max_tokens=self.config.llm_max_tokens,
                timeout_seconds=self.config.llm_timeout_seconds,
            )
        return DeterministicLLM()

    async def analyze(
        self, description: str, question: str, session_id: str = "default"
    ) -> dict[str, Any]:
        if self._closed:
            raise EngineExecutionError("engine is closed")
        if not description.strip() or not question.strip() or not session_id.strip():
            raise ValueError("description, question, and session_id are required")
        trace = TraceRecorder(self.config.trace_dir)
        try:
            trace.record(
                "run_start",
                {
                    "session_id": session_id,
                    "runtime_mode": self.mode,
                    "retrieval_mode": self.retrieval_mode,
                    "skills": {
                        agent: {"skill_name": skill.name, "skill_sha256": skill.sha256}
                        for agent, skill in self.skills.items()
                    },
                },
                stage="lifecycle",
            )
            trace.record(
                "context_built", {"description": description, "question": question}, stage="context"
            )
            current_context = {"description": description, "question": question}
            current_input = f"{description}\nQuestion: {question}"
            memory_context = self.memory.context(session_id, current_input)
            injected_memory = memory_context[:-1]
            trace.record(
                "memory_read",
                {
                    "injected_messages": injected_memory,
                    "current_input": memory_context[-1],
                    "long_term_memory": "excluded",
                },
                stage="memory",
            )
            contract = build_answer_contract(question)
            trace.record("contract_built", contract.to_dict(), stage="context")
            ledger = build_evidence_ledger(description)
            trace.record("evidence_ledger_built", ledger.to_dict(), stage="context")

            plan = await Planner(self.llm).plan(
                question,
                contract,
                ledger,
                trace,
                current_context=current_context,
                memory_context=injected_memory,
            )
            trace.record("plan_created", plan.to_dict(), stage="planning")
            route = Router().route(plan)
            trace.record("route_selected", route.to_dict(), stage="routing")

            evidence_bundle = EvidenceBundle("", "", [])
            tools = self._tool_registry(
                question, contract.requested_deliverables[0], evidence_bundle
            )
            loop = AgentLoop(self.llm, tools, self.config.max_tool_calls)
            tasks = [
                asyncio.wait_for(
                    loop.execute(
                        AGENTS[subtask.assigned_agent],
                        subtask,
                        contract,
                        ledger,
                        evidence_bundle.compact(),
                        current_context,
                        injected_memory,
                        self.skills[subtask.assigned_agent],
                        trace,
                    ),
                    timeout=self.config.worker_timeout_seconds,
                )
                for subtask in plan.subtasks
            ]
            outcomes = await asyncio.gather(*tasks, return_exceptions=True)
            workers: list[WorkerResult] = []
            for subtask, outcome in zip(plan.subtasks, outcomes, strict=True):
                if isinstance(outcome, BaseException):
                    result = WorkerResult(
                        subtask.assigned_agent,
                        subtask.subtask_id,
                        "",
                        False,
                        tools.calls_for(subtask.assigned_agent),
                    )
                    trace.record(
                        "error",
                        {"where": "worker", "error": type(outcome).__name__},
                        stage="worker",
                        agent=subtask.assigned_agent,
                    )
                    workers.append(result)
                else:
                    workers.append(outcome)
            successful = [item for item in workers if item.success and item.answer.strip()]
            if not successful:
                raise EngineExecutionError("all workers failed")

            if route.mode == "multi":
                final_draft = await self._synthesize(
                    question, contract.to_dict(), successful, trace
                )
            else:
                final_draft = successful[0].answer
            final_draft = sanitize_answer(final_draft)
            checker_input = trace.record(
                "checker_input", {"draft": final_draft}, stage="guardrail", agent="guardrail"
            )
            check = Guardrail().check(final_draft, contract, ledger)
            trace.record(
                "checker_result",
                check.to_dict(),
                stage="guardrail",
                agent="guardrail",
                parent_event_id=checker_input,
            )
            patch = apply_stable_edits(StableDraft.parse(final_draft), check.edits)
            final_answer = sanitize_answer(patch.answer)
            trace.record(
                "patch_applied",
                {"applied": patch.applied, "reason": patch.reason},
                stage="guardrail",
                agent="guardrail",
            )

            self.memory.add(session_id, "user", f"{description}\nQuestion: {question}")
            self.memory.add(session_id, "assistant", final_answer)
            trace.record(
                "memory_write", {"session_id": session_id, "messages_added": 2}, stage="memory"
            )
            trace.record("final_answer", {"answer": final_answer}, stage="output")
            trace.finish("completed")
            summary = trace.summary()
            developer = {
                "plan": plan.to_dict(),
                "route": route.to_dict(),
                "workers": [item.to_dict() for item in workers],
                "tool": {"max_calls_per_worker": self.config.max_tool_calls},
                "retrieval": {
                    "query": evidence_bundle.query,
                    "collection": evidence_bundle.collection,
                    "retrieved_count": len(evidence_bundle.items),
                    "admitted_evidence_ids": [
                        item.evidence_id for item in evidence_bundle.admitted_items
                    ],
                },
                "guardrail": check.to_dict(),
                "trace_events": [event.to_dict() for event in trace.events],
            }
            presentation = (
                PresentationAdapter()
                .adapt(final_answer, contract, ledger, evidence_bundle, summary, developer)
                .to_dict()
            )
            return {
                "final_answer": final_answer,
                "run_id": trace.run_id,
                "trace": summary,
                "presentation": presentation,
            }
        except Exception as error:
            trace.record(
                "error", {"error": type(error).__name__, "message": str(error)}, stage="lifecycle"
            )
            trace.finish("failed")
            raise

    def _tool_registry(
        self, question: str, deliverable: str, bundle: EvidenceBundle
    ) -> ToolRegistry:
        registry = ToolRegistry(self.config.max_tool_calls)
        collection_router = CollectionRouter(
            RetrievalConfig(
                generic_collection=self.config.generic_collection,
                special_collection=self.config.special_collection,
                top_k=self.config.retrieval_top_k,
                admission_threshold=self.config.retrieval_threshold,
            )
        )

        async def retrieve(**arguments: Any) -> dict[str, Any]:
            tool_name = str(arguments.pop("_tool_name", "search_knowledge"))
            tool_input = " ".join(str(value) for value in arguments.values())
            query = QueryBuilder().build(question, tool_input, deliverable, tool_input)
            decision = collection_router.route(tool_name)
            items = await self.retrieval_backend.search(
                query, decision.collection, self.config.retrieval_top_k
            )
            admit_evidence(items, self.config.retrieval_threshold, self.config.retrieval_top_k)
            bundle.query = query
            bundle.collection = decision.collection
            known = {item.evidence_id for item in bundle.items}
            bundle.items.extend(item for item in items if item.evidence_id not in known)
            compact = EvidenceBundle(query, decision.collection, items).compact()
            return {
                "query": query,
                "collection": decision.collection,
                "admitted": compact,
                "_trace_retrieval": {
                    "query": query,
                    "collection": decision.collection,
                    "routing_reason": decision.reason,
                    "top_k": self.config.retrieval_top_k,
                    "raw_candidates": [item.to_dict() for item in items],
                    "admission": [
                        {
                            "evidence_id": item.evidence_id,
                            "admitted": item.admitted,
                            "reason": item.admission_reason,
                            "score": item.score,
                        }
                        for item in items
                    ],
                    "compact_evidence": compact,
                },
            }

        for name in RETRIEVAL_TOOLS:

            async def handler(_name: str = name, **kwargs: Any) -> dict[str, Any]:
                return await retrieve(_tool_name=_name, **kwargs)

            registry.register(name, handler)
        registry.register(
            "analyze_symptoms",
            lambda symptoms: {
                "input": symptoms,
                "notice": "No deterministic diagnosis is produced by this utility.",
            },
        )
        registry.register(
            "assess_risk",
            lambda symptoms: {
                "input": symptoms,
                "notice": "Escalate urgent warning signs for qualified review.",
            },
        )
        return registry

    async def _synthesize(
        self,
        question: str,
        contract: dict[str, Any],
        workers: list[WorkerResult],
        trace: TraceRecorder,
    ) -> str:
        payload = {
            "question": question,
            "contract": contract,
            "worker_drafts": [item.to_dict() for item in workers],
        }
        input_id = trace.record("synthesis_input", payload, stage="synthesis", agent="synthesizer")
        messages = [
            {
                "role": "system",
                "content": (
                    "Combine completed worker drafts without adding patient facts or hidden "
                    "reasoning.\n\nPublic procedural skill (synthesis):\n"
                    f"{self.skills['synthesizer'].instructions}"
                ),
            },
            {
                "role": "user",
                "content": "Synthesize this payload:\n" + json.dumps(payload, ensure_ascii=False),
            },
        ]
        response, response_id = await complete_with_trace(
            self.llm,
            trace,
            stage="synthesis",
            agent="synthesizer",
            messages=messages,
            purpose="synthesis",
            parent_event_id=input_id,
        )
        trace.record(
            "synthesis_output",
            {"draft": response.content},
            stage="synthesis",
            agent="synthesizer",
            parent_event_id=response_id,
        )
        return response.content

    async def close(self) -> None:
        if not self._closed:
            await self.llm.close()
            self._closed = True
