from __future__ import annotations

import asyncio
import inspect
import json
from time import perf_counter
from typing import Any

from medagent.agents.base import AgentDefinition, WorkerResult
from medagent.agents.consultation import CONSULTATION_AGENT
from medagent.agents.diagnostic import DIAGNOSTIC_AGENT
from medagent.agents.research import RESEARCH_AGENT
from medagent.context.contract import build_answer_contract
from medagent.context.evidence_ledger import build_evidence_ledger
from medagent.context.request_spec import RequestSpec, build_request_spec
from medagent.guardrails.contract_checker import Guardrail
from medagent.guardrails.sanitizer import sanitize_answer
from medagent.guardrails.stable_patch import StableDraft, apply_stable_edits
from medagent.llm.client import LLMClient, LLMResponse, OpenAICompatibleLLM
from medagent.llm.fakes import DeterministicLLM
from medagent.llm.generation import GenerationPolicy, run_with_length_recovery
from medagent.memory.session import SessionMemory
from medagent.observability.tracer import TraceRecorder
from medagent.planning.complexity import (
    ResponseProfile,
    TaskComplexityProfile,
    build_complexity_profile,
    build_response_profile,
    tool_capabilities,
)
from medagent.planning.planner import Planner, apply_contract_policy
from medagent.planning.router import Router
from medagent.presentation.adapter import PresentationAdapter
from medagent.presentation.transform import PresentationTransform, transform_presentation
from medagent.retrieval.admission import admit_evidence
from medagent.retrieval.backend import RetrievalBackend
from medagent.retrieval.collection_router import CollectionRouter
from medagent.retrieval.config import RetrievalConfig
from medagent.retrieval.evidence import EvidenceBundle
from medagent.retrieval.factory import build_retrieval_backend
from medagent.retrieval.query_builder import QueryBuilder
from medagent.runtime.agent_loop import AgentLoop
from medagent.runtime.config import RuntimeConfig
from medagent.runtime.coverage import evaluate_contract_coverage, evaluate_request_coverage
from medagent.runtime.engine import EngineExecutionError, StageGenerationError
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
        presentation_llm: LLMClient | None = None,
        retrieval_backend: RetrievalBackend | None = None,
        memory: SessionMemory | None = None,
    ) -> None:
        self.config = config or RuntimeConfig.from_env()
        self.llm = llm or self._build_llm()
        # Scripted/offline runtimes retain their deterministic presentation unless
        # a presentation client is explicitly supplied. Real provider runs transform once.
        self.presentation_llm = presentation_llm or (
            self.llm if isinstance(self.llm, OpenAICompatibleLLM) else None
        )
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
            request_spec = build_request_spec(question)
            trace.record("request_spec_built", request_spec.to_dict(), stage="context")
            contract = build_answer_contract(question)
            trace.record("contract_built", contract.to_dict(), stage="context")
            complexity = build_complexity_profile(question, contract)
            response_profile = build_response_profile(complexity)
            trace.record(
                "complexity_profile_built", complexity.to_dict(), stage="planning"
            )
            trace.record(
                "response_profile_built", response_profile.to_dict(), stage="planning"
            )
            ledger = build_evidence_ledger(description)
            trace.record("evidence_ledger_built", ledger.to_dict(), stage="context")

            plan = await Planner(
                self.llm,
                max_tokens=self.config.planner_max_tokens,
                max_length_recoveries=self.config.planner_max_length_recoveries,
            ).plan(
                question,
                contract,
                ledger,
                trace,
                current_context=current_context,
                memory_context=injected_memory,
                complexity=complexity,
                request_spec=request_spec,
            )
            plan = apply_contract_policy(plan, contract, complexity, request_spec)
            trace.record("plan_created", plan.to_dict(), stage="planning")
            route = Router().route(plan, complexity)
            trace.record("route_selected", route.to_dict(), stage="routing")

            evidence_bundle = EvidenceBundle("", "", [])
            capabilities = tool_capabilities(complexity)
            tools = self._tool_registry(
                question,
                contract.requested_deliverables[0],
                evidence_bundle,
                capabilities,
            )
            trace.record(
                "capability_policy_applied",
                {
                    "requires_external_evidence": complexity.requires_external_evidence,
                    "allowed_tools": {
                        worker: sorted(names) for worker, names in capabilities.items()
                    },
                },
                stage="routing",
            )
            loop = AgentLoop(
                self.llm,
                tools,
                self.config.max_tool_calls,
                max_tokens=self.config.worker_max_tokens,
                max_length_recoveries=self.config.worker_max_length_recoveries,
                max_infrastructure_retries=self.config.worker_max_infrastructure_retries,
            )
            tasks = [
                asyncio.wait_for(
                    loop.execute(
                        AGENTS[subtask.assigned_agent],
                        subtask,
                        request_spec,
                        contract,
                        ledger,
                        current_context,
                        injected_memory,
                        self.skills[subtask.assigned_agent],
                        trace,
                        complexity,
                        response_profile,
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
                        worker=subtask.assigned_agent,
                        subtask_id=subtask.subtask_id,
                        answer="",
                        success=False,
                        tool_calls=tools.calls_for(subtask.assigned_agent),
                        generation_status="provider_error",
                        failure_reason="provider_error",
                        worker_status="provider_error",
                    )
                    trace.record(
                        "error",
                        {"where": "worker", "error": type(outcome).__name__},
                        stage="worker",
                        agent=subtask.assigned_agent,
                    )
                    trace.record(
                        "worker_draft",
                        result.to_dict(),
                        stage="worker",
                        agent=subtask.assigned_agent,
                    )
                    workers.append(result)
                else:
                    workers.append(outcome)
            successful = [item for item in workers if item.success and item.answer.strip()]
            coverage = evaluate_contract_coverage(contract, plan.subtasks, workers)
            trace.record("contract_coverage", coverage.to_dict(), stage="contract_completion")
            request_coverage = evaluate_request_coverage(request_spec, workers)
            trace.record(
                "request_coverage",
                request_coverage.to_dict(),
                stage="request_completion",
            )
            if not successful:
                raise EngineExecutionError("all workers failed")
            if not request_coverage.complete or not coverage.complete:
                failure_reason = (
                    "missing_required_request_items"
                    if not request_coverage.complete
                    else "missing_required_deliverables"
                )
                trace.record(
                    "error",
                    {
                        "failure_stage": "request_completion",
                        "failure_reason": failure_reason,
                        "missing_required_request_items": (
                            request_coverage.missing_request_items
                        ),
                        "missing_required_deliverables": (
                            coverage.missing_required_deliverables
                        ),
                    },
                    stage="contract_completion",
                )
                trace.finish("incomplete")
                summary = trace.summary()
                developer = {
                    "plan": plan.to_dict(),
                    "route": route.to_dict(),
                    "complexity_profile": complexity.to_dict(),
                    "response_profile": response_profile.to_dict(),
                    "workers": [item.to_dict() for item in workers],
                    "request_spec": request_spec.to_dict(),
                    "request_coverage": request_coverage.to_dict(),
                    "contract_coverage": coverage.to_dict(),
                    "failure_stage": "request_completion",
                    "failure_reason": failure_reason,
                    "tool": {"max_calls_per_worker": self.config.max_tool_calls},
                    "retrieval": {
                        "query": evidence_bundle.query,
                        "collection": evidence_bundle.collection,
                        "retrieved_count": len(evidence_bundle.items),
                        "admitted_evidence_ids": [
                            item.evidence_id for item in evidence_bundle.admitted_items
                        ],
                    },
                    "trace_events": [event.to_dict() for event in trace.events],
                }
                presentation = (
                    PresentationAdapter()
                    .adapt("", contract, ledger, evidence_bundle, summary, developer)
                    .to_dict()
                )
                return {
                    "final_answer": "",
                    "status": "incomplete",
                    "user_request_complete": request_coverage.complete,
                    "contract_complete": coverage.complete,
                    "missing_required_request_items": (
                        request_coverage.missing_request_items
                    ),
                    "missing_required_deliverables": (
                        coverage.missing_required_deliverables
                    ),
                    "request_item_answers": request_coverage.request_item_answers,
                    "successful_workers": coverage.successful_workers,
                    "failed_workers": coverage.failed_workers,
                    "run_id": trace.run_id,
                    "trace": summary,
                    "presentation": presentation,
                }

            if len(successful) >= 2:
                final_draft = await self._synthesize(
                    question,
                    request_spec,
                    contract.to_dict(),
                    successful,
                    trace,
                    complexity,
                    response_profile,
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
            transformed: PresentationTransform | None = None
            if self.presentation_llm is not None:
                presentation_started = perf_counter()
                try:
                    transformed, presentation_response = await transform_presentation(
                        self.presentation_llm, description, question, final_answer, request_spec
                    )
                    trace.record(
                        "presentation_transform",
                        {
                            "protocol": "tagged_text",
                            "status": "success",
                            "parse_status": "valid",
                            "required_request_item_ids": request_spec.required_item_ids,
                            "direct_covered_request_item_ids": [
                                section.request_item_id
                                for section in (transformed.direct_answer_sections or [])
                            ],
                            "finish_reason": presentation_response.finish_reason,
                            "content_length": len(presentation_response.content),
                            "latency_ms": round((perf_counter() - presentation_started) * 1000, 2),
                            "usage": presentation_response.usage,
                            "model": presentation_response.model
                            or getattr(self.presentation_llm, "model", None),
                        },
                        stage="presentation",
                    )
                except Exception as presentation_error:
                    failed_response = getattr(presentation_error, "response", None)
                    provider_response = (
                        failed_response if isinstance(failed_response, LLMResponse) else None
                    )
                    trace.record(
                        "presentation_transform",
                        {
                            "protocol": "tagged_text",
                            "status": "fallback",
                            "parse_status": "invalid" if provider_response else "provider_error",
                            "latency_ms": round((perf_counter() - presentation_started) * 1000, 2),
                            "usage": provider_response.usage if provider_response else {},
                            "model": (provider_response.model if provider_response else None)
                            or getattr(self.presentation_llm, "model", None),
                            "finish_reason": (
                                provider_response.finish_reason if provider_response else None
                            ),
                            "content_length": (
                                len(provider_response.content) if provider_response else None
                            ),
                            "raw_output": (
                                provider_response.content if provider_response else None
                            ),
                            "error_type": getattr(
                                presentation_error, "cause_type", type(presentation_error).__name__
                            ),
                            "failure_reason": getattr(
                                presentation_error,
                                "failure_reason",
                                type(presentation_error).__name__,
                            ),
                        },
                        stage="presentation",
                    )
            trace.finish("completed")
            summary = trace.summary()
            developer = {
                "plan": plan.to_dict(),
                "route": route.to_dict(),
                "complexity_profile": complexity.to_dict(),
                "response_profile": response_profile.to_dict(),
                "workers": [item.to_dict() for item in workers],
                "request_spec": request_spec.to_dict(),
                "request_coverage": request_coverage.to_dict(),
                "contract_coverage": coverage.to_dict(),
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
                .adapt(
                    final_answer, contract, ledger, evidence_bundle, summary, developer, transformed
                )
                .to_dict()
            )
            return {
                "final_answer": final_answer,
                "status": "completed",
                "user_request_complete": True,
                "contract_complete": True,
                "missing_required_request_items": [],
                "missing_required_deliverables": [],
                "request_item_answers": request_coverage.request_item_answers,
                "successful_workers": coverage.successful_workers,
                "failed_workers": coverage.failed_workers,
                "run_id": trace.run_id,
                "trace": summary,
                "presentation": presentation,
            }
        except Exception as error:
            error_payload = {"error": type(error).__name__, "message": str(error)}
            if isinstance(error, StageGenerationError):
                error_payload.update(
                    {
                        "failure_stage": error.failure_stage,
                        "failure_reason": error.failure_reason,
                    }
                )
            trace.record("error", error_payload, stage="lifecycle")
            trace.finish("failed")
            raise

    def _tool_registry(
        self,
        question: str,
        deliverable: str,
        bundle: EvidenceBundle,
        capabilities: dict[str, set[str]],
    ) -> ToolRegistry:
        registry = ToolRegistry(self.config.max_tool_calls, capabilities)
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
                "evidence_status": (
                    "relevant_evidence_admitted" if compact else "no_relevant_evidence"
                ),
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
        request_spec: RequestSpec,
        contract: dict[str, Any],
        workers: list[WorkerResult],
        trace: TraceRecorder,
        complexity: TaskComplexityProfile,
        response_profile: ResponseProfile,
    ) -> str:
        payload = {
            "question": question,
            "request_spec": request_spec.to_dict(),
            "contract": contract,
            "complexity_profile": complexity.to_dict(),
            "response_profile": response_profile.to_dict(),
            "worker_drafts": [item.to_dict() for item in workers],
        }
        input_id = trace.record("synthesis_input", payload, stage="synthesis", agent="synthesizer")
        messages = [
            {
                "role": "system",
                "content": (
                    "Produce the smallest complete answer that satisfies every required item in "
                    "the RequestSpec and the AnswerContract. Preserve an answer for every required "
                    "request_item_id; do not omit one during synthesis. "
                    "Answer requested deliverables and must_cover items first. Remove repetition "
                    "and unrelated worker expansion; do not add new deliverables. Preserve only "
                    "the rationale needed for the medical conclusion and necessary safety "
                    "warnings. Do not add patient facts or hidden reasoning.\n\n"
                    "Public procedural skill (synthesis):\n"
                    f"{self.skills['synthesizer'].instructions}"
                ),
            },
            {
                "role": "user",
                "content": "Synthesize this payload:\n" + json.dumps(payload, ensure_ascii=False),
            },
        ]
        generation = await run_with_length_recovery(
            self.llm,
            policy=GenerationPolicy(
                self.config.synthesis_max_tokens,
                self.config.synthesis_max_length_recoveries,
            ),
            stage="synthesis",
            agent="synthesizer",
            messages=messages,
            purpose="synthesis",
            is_complete=lambda response: bool(response.content.strip()),
            trace=trace,
            parent_event_id=input_id,
        )
        response = generation.response
        trace.record(
            "synthesis_output",
            {
                "draft": response.content,
                "generation_status": generation.generation_status,
                "length_recovery_count": generation.length_recovery_count,
            },
            stage="synthesis",
            agent="synthesizer",
            parent_event_id=generation.response_id,
        )
        if generation.generation_status == "length_exhausted":
            raise StageGenerationError("synthesis", "generation_length_exhausted")
        if not response.content.strip():
            raise StageGenerationError("synthesis", "empty_generation_output")
        return response.content

    async def close(self) -> None:
        if not self._closed:
            if self.presentation_llm is not None and self.presentation_llm is not self.llm:
                await self.presentation_llm.close()
            await self.llm.close()
            retrieval_close = getattr(self.retrieval_backend, "close", None)
            if retrieval_close is not None:
                result = retrieval_close()
                if inspect.isawaitable(result):
                    await result
            self._closed = True
