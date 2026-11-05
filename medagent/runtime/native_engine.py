from __future__ import annotations

import asyncio
import inspect
import json
from copy import deepcopy
from time import perf_counter
from typing import Any
from uuid import uuid4

from medagent.agents.base import AgentDefinition, WorkerResult
from medagent.agents.consultation import CONSULTATION_AGENT
from medagent.agents.diagnostic import DIAGNOSTIC_AGENT
from medagent.agents.research import RESEARCH_AGENT
from medagent.context.contract import AnswerContract, build_answer_contract
from medagent.context.evidence_ledger import EvidenceLedger, build_evidence_ledger
from medagent.context.request_spec import RequestSpec, build_request_spec
from medagent.guardrails.contract_checker import CheckResult
from medagent.guardrails.sanitizer import sanitize_answer
from medagent.guardrails.untrusted_context import (
    CONTEXT_BOUNDARY,
    contains_instruction_spoof,
    context_risk,
)
from medagent.llm.client import LLMClient, LLMResponse, OpenAICompatibleLLM
from medagent.llm.fakes import DeterministicLLM
from medagent.llm.generation import GenerationPolicy, run_with_length_recovery
from medagent.memory.session import SessionMemory, resolve_session_id
from medagent.observability.tracer import TraceRecorder
from medagent.planning.complexity import (
    ResponseProfile,
    TaskComplexityProfile,
    build_complexity_profile,
    build_response_profile,
    tool_capabilities,
)
from medagent.planning.models import PlanBudgetError, Subtask
from medagent.planning.planner import Planner, apply_contract_policy
from medagent.planning.router import Router
from medagent.presentation.adapter import PresentationAdapter
from medagent.presentation.transform import PresentationTransform, transform_presentation
from medagent.retrieval.admission import admit_evidence
from medagent.retrieval.backend import (
    FakeRetrievalBackend,
    RetrievalBackend,
    RetrievalDisabledError,
)
from medagent.retrieval.collection_router import CollectionRouter
from medagent.retrieval.config import RetrievalConfig
from medagent.retrieval.evidence import EvidenceBundle, declared_source_kind
from medagent.retrieval.factory import build_retrieval_backend
from medagent.retrieval.query_builder import QueryBuilder
from medagent.runtime.acceptance import FinalAcceptance, finalize_answers
from medagent.runtime.agent_loop import AgentLoop
from medagent.runtime.config import RuntimeConfig
from medagent.runtime.engine import EngineExecutionError, StageGenerationError
from medagent.runtime.evaluation_guard import EvaluationRunGuard
from medagent.runtime.evaluation_inputs import (
    EvaluationRun,
    FrozenExecutionPlan,
    FrozenRequest,
    canonical_json,
    content_sha256,
    memory_conditions,
)
from medagent.runtime.handoff import WorkerHandoff, build_handoff, evidence_transfers
from medagent.runtime.semantic_validation import (
    SemanticFailure,
    SemanticValidator,
    contract_for_spec,
)
from medagent.skills.loader import load_public_skills
from medagent.tools.registry import ToolExecutionContext, ToolRegistry
from medagent.tools.schemas import RETRIEVAL_TOOLS

AGENTS: dict[str, AgentDefinition] = {
    item.agent_id: item for item in (DIAGNOSTIC_AGENT, CONSULTATION_AGENT, RESEARCH_AGENT)
}


class _RetrievalToolError(RuntimeError):
    def __init__(self, error: Exception, trace_context: dict[str, Any]) -> None:
        super().__init__("retrieval execution failed")
        self.retrieval_error_type = type(error).__name__
        self.retrieval_trace = trace_context


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
        validation_llm: LLMClient | None = None,
    ) -> None:
        self.config = config or RuntimeConfig.from_env()
        self.llm = llm or self._build_llm()
        self.validation_llm = validation_llm or self.llm
        # Scripted/offline runtimes retain their deterministic presentation unless
        # a presentation client is explicitly supplied. Real provider runs transform once.
        self.presentation_llm = presentation_llm or (
            self.llm if isinstance(self.llm, OpenAICompatibleLLM) else None
        )
        self.retrieval_backend = retrieval_backend or build_retrieval_backend(self.config)
        self.retrieval_mode = (
            self.config.retrieval_mode if retrieval_backend is None else "injected"
        )
        self.memory = (
            memory if memory is not None
            else SessionMemory(token_budget=self.config.memory_token_budget)
        )
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
        self, description: str, question: str, session_id: str | None = None
    ) -> dict[str, Any]:
        return await self._analyze(description, question, session_id)

    async def analyze_frozen(
        self, request: FrozenRequest, session_id: str | None = None, *,
        plan: FrozenExecutionPlan | None = None,
    ) -> EvaluationRun:
        """Trusted Python execution-layer evaluation; no HTTP/API exposure."""
        if not self.config.semantic_validation_enabled:
            raise ValueError("frozen execution requires W2 semantic validation")
        request.materialize(request.description, request.question)
        if plan is not None and self.config.max_concurrent_workers != 1:
            raise ValueError("C-serial replay requires max_concurrent_workers=1")
        exported: list[FrozenExecutionPlan] = []
        result = await self._analyze(
            request.description, request.question, session_id,
            frozen_request=request, replay_plan=plan, exported=exported,
        )
        return EvaluationRun(result, exported[0] if exported else None)

    def _evaluation_models(self) -> dict[str, Any]:
        def model_settings(client: LLMClient | None) -> dict[str, Any] | None:
            if client is None:
                return None
            return {key: getattr(client, key, None)
                    for key in ("model", "temperature", "max_tokens", "base_url")}

        return {
            "model": model_settings(self.llm),
            "validation_model": model_settings(self.validation_llm),
            "presentation_enabled": self.presentation_llm is not None,
            "presentation_model": model_settings(self.presentation_llm),
        }

    def _evaluation_retrieval(self) -> dict[str, Any]:
        backend = self.retrieval_backend
        return {
            "retrieval_mode": self.retrieval_mode,
            "backend_type": f"{type(backend).__module__}.{type(backend).__qualname__}",
            "fake_fixture": ([item.to_dict() for item in backend.items]
                             if isinstance(backend, FakeRetrievalBackend) else None),
        }

    def _evaluation_conditions(
        self, history: list[dict[str, str]], tools: ToolRegistry,
    ) -> dict[str, Any]:
        settings = self.config.public_dict()
        # Trace destination is not an execution condition; concurrency is the treatment.
        for key in ("trace_dir", "max_concurrent_workers"):
            settings.pop(key)
        conditions = {
            "settings": settings, "memory": memory_conditions(self.memory, history),
            **self._evaluation_models(),
            **self._evaluation_retrieval(),
            "skills": {key: skill.sha256 for key, skill in self.skills.items()},
            "tools": {worker: tools.schemas_for(worker) for worker in AGENTS},
        }
        if tools.evidence_requirements is not None:
            conditions["external_evidence_requirements"] = [
                r.to_dict() for r in tools.evidence_requirements
            ]
        return conditions

    async def _analyze(
        self, description: str, question: str, session_id: str | None = None, *,
        frozen_request: FrozenRequest | None = None,
        replay_plan: FrozenExecutionPlan | None = None,
        exported: list[FrozenExecutionPlan] | None = None,
    ) -> dict[str, Any]:
        if self._closed:
            raise EngineExecutionError("engine is closed")
        session_id = resolve_session_id(session_id)
        if not description.strip() or not question.strip():
            raise ValueError("description, question, and session_id are required")
        trace = TraceRecorder(
            self.config.trace_dir, content_mode=self.config.trace_content_mode,
            secrets=(self.config.llm_api_key, self.config.milvus_token),
        )
        guard: EvaluationRunGuard | None = None
        run_llm, run_validation_llm = self.llm, self.validation_llm
        run_presentation_llm = self.presentation_llm
        evidence_declarations = frozen_request.evidence_requirements if frozen_request else None
        try:
            if frozen_request is not None:
                guard = EvaluationRunGuard()
                guard.watch("model_parameters_drift", lambda: {
                    "clients": [id(self.llm), id(self.validation_llm), id(self.presentation_llm)],
                    **self._evaluation_models(),
                })
                guard.watch("runtime_configuration_drift", lambda: self.config.public_dict())
                guard.watch("memory_configuration_drift", lambda: {
                    "instance": id(self.memory), **memory_conditions(self.memory, []),
                })
                guard.watch("retrieval_conditions_drift", lambda: {
                    "instance": id(self.retrieval_backend), **self._evaluation_retrieval(),
                })
                run_llm = guard.wrap(self.llm, temperature=self.config.llm_temperature,
                                     max_tokens=self.config.llm_max_tokens)
                run_validation_llm = guard.wrap(
                    self.validation_llm, temperature=self.config.llm_temperature,
                    max_tokens=self.config.llm_max_tokens,
                )
                run_presentation_llm = guard.wrap(
                    self.presentation_llm, temperature=self.config.llm_temperature,
                    max_tokens=self.config.llm_max_tokens,
                )
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
            if evidence_declarations is not None:
                current_context["external_evidence_requirements"] = canonical_json([
                    r.to_dict() for r in evidence_declarations
                ])
            current_input = f"{description}\nQuestion: {question}"
            memory_context, memory_metadata = self.memory.context_with_metadata(
                session_id, current_input
            )
            injected_memory = [item for item in memory_context[:-1]
                               if not contains_instruction_spoof(item["content"])]
            if guard is not None:
                guard.check()
                injected_memory = json.loads(canonical_json(injected_memory))
            quarantined = len(memory_context[:-1]) - len(injected_memory)
            if quarantined:
                trace.record("context_quarantined", {"count": quarantined}, stage="memory")
            risk_count = sum(context_risk(item["content"]) == "suspicious"
                             for item in injected_memory)
            if risk_count:
                trace.record("context_risk", {"context_risk_count": risk_count}, stage="memory")
            trace.record(
                "memory_read",
                {
                    "injected_messages": injected_memory,
                    "current_input": memory_context[-1],
                    "long_term_memory": "excluded",
                    **memory_metadata,
                    "quarantined_history_messages": quarantined,
                    "injected_history_messages": len(injected_memory),
                },
                stage="memory",
            )
            validator = SemanticValidator(
                run_validation_llm, self.config, trace, memory_context=injected_memory,
            )
            restrictions: list[str] = []
            semantic: dict[str, Any] = {
                "status": "disabled", "complete": None, "calls": 0, "items": [],
                "assurance": "structural_only",
            }
            if frozen_request is not None:
                request_spec, contract, restrictions = frozen_request.materialize(
                    description, question,
                )
                # W1 is deliberately bypassed only in this explicit execution-layer path.
                validator.require_calls(1)
                trace.record("request_spec_validation", {
                    "status": "accepted", "request_source": "frozen",
                    "request_sha256": frozen_request.sha256, "calls": 0,
                }, stage="context")
            elif self.config.semantic_validation_enabled:
                try:
                    # Do not start extraction/business work if full W1 + W2 cannot fit.
                    validator.require_calls(3)
                    request_spec, restrictions = await validator.extract(
                        question, description=description,
                    )
                    validator.require_calls(1)  # Reserve W2 before Planner dispatch.
                except SemanticFailure as error:
                    semantic = {"status": error.status, "reason": error.reason,
                                "complete": False, "calls": validator.calls, "items": []}
                    trace.record("request_spec_validation", semantic, stage="context")
                    trace.record("semantic_acceptance", semantic, stage="final_completion")
                    trace.record("final_answer", {"answer": ""}, stage="output")
                    trace.finish("incomplete")
                    summary = trace.summary()
                    presentation = PresentationAdapter().adapt(
                        "", build_answer_contract(question), build_evidence_ledger(description),
                        EvidenceBundle("", "", []), summary,
                        {"semantic_validation": semantic} if self.config.trace_content_mode == "raw"
                        else {},
                    ).to_dict()
                    if self.config.trace_content_mode == "metadata":
                        presentation["execution_summary"] = summary
                    return {
                        "session_id": session_id,
                        "final_answer": "", "status": "incomplete",
                        "user_request_complete": False, "contract_complete": False,
                        "structural_user_request_complete": False,
                        "structural_contract_complete": False,
                        "semantic_validation": semantic,
                        "missing_required_request_items": [], "missing_required_deliverables": [],
                        "request_item_answers": {}, "final_request_item_answers": {},
                        "successful_workers": 0, "failed_workers": 0,
                        "run_id": trace.run_id, "trace": summary, "presentation": presentation,
                    }
                contract = contract_for_spec(request_spec, restrictions)
            else:
                request_spec = build_request_spec(question)
                contract = build_answer_contract(question)
            trace.record("request_spec_built", request_spec.to_dict(), stage="context")
            trace.record("contract_built", contract.to_dict(), stage="context")
            if evidence_declarations is None:
                complexity = build_complexity_profile(question, contract, request_spec)
            else:
                complexity = build_complexity_profile(
                    question, contract, request_spec,
                    frozen_evidence_requirements=evidence_declarations,
                )
            response_profile = build_response_profile(complexity)
            trace.record("complexity_profile_built", complexity.to_dict(), stage="planning")
            trace.record("response_profile_built", response_profile.to_dict(), stage="planning")
            ledger = build_evidence_ledger(description)
            trace.record("evidence_ledger_built", ledger.to_dict(), stage="context")
            trace.record("runtime_budget", {
                "max_subtasks": self.config.max_subtasks,
                "max_concurrent_workers": self.config.max_concurrent_workers,
                "max_model_rounds": self.config.worker_max_model_rounds,
                "max_tool_calls": self.config.max_tool_calls,
                "worker_timeout_seconds": self.config.worker_timeout_seconds,
            }, stage="planning")

            # Construct evaluation tool schemas before replay validation, without executing tools.
            if frozen_request is not None:
                evidence_bundle = EvidenceBundle("", "", [])
                evidence_bundle.case_description = description
                evidence_bundle.evidence_requirements = evidence_declarations
                capabilities = tool_capabilities(complexity)
                tools = self._tool_registry(
                    question, evidence_bundle, capabilities, evaluation_guard=guard,
                )
                guard.check()
                guard.watch("tool_permissions_drift", lambda: {
                    worker: tools.schemas_for(worker) for worker in AGENTS
                })
                if evidence_declarations is not None:
                    guard.watch("tool_permissions_drift", lambda: {
                        "tools_evidence": [r.to_dict() for r in tools.evidence_requirements],
                        "bundle_evidence": [r.to_dict()
                                            for r in evidence_bundle.evidence_requirements],
                    })
                conditions = json.loads(canonical_json(
                    self._evaluation_conditions(injected_memory, tools),
                ))
            if replay_plan is not None:
                plan = replay_plan.validate_for(
                    frozen_request, conditions, self.config.max_subtasks,
                )
            else:
                plan = await Planner(
                    run_llm,
                    max_tokens=self.config.planner_max_tokens,
                    max_length_recoveries=self.config.planner_max_length_recoveries,
                    max_subtasks=self.config.max_subtasks,
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
                if guard is not None:
                    guard.check()
                plan = apply_contract_policy(plan, contract, complexity, request_spec)
            if guard is not None:
                guard.check()
            plan.validate_budget(self.config.max_subtasks)
            plan_payload = plan.to_dict()
            if frozen_request is not None:
                snapshot = FrozenExecutionPlan.capture(plan, frozen_request, conditions)
                snapshot.validate_for(frozen_request, conditions, self.config.max_subtasks)
                if exported is not None:
                    exported.append(snapshot)
                plan_payload = {**plan_payload,
                    "plan_source": "frozen_replay" if replay_plan is not None else "planner_policy",
                    "plan_sha256": snapshot.sha256,
                    "execution_conditions_sha256": content_sha256(conditions),
                    "planner_call_count": sum(e.event_type == "llm_request"
                                              and e.stage == "planning" for e in trace.events),
                }
            plan_event_id = trace.record("plan_created", plan_payload, stage="planning")
            route = Router().route(plan, complexity)
            route_event_id = trace.record(
                "route_selected", route.to_dict(), stage="routing", parent_event_id=plan_event_id
            )

            if frozen_request is None:
                evidence_bundle = EvidenceBundle("", "", [])
                evidence_bundle.case_description = description
                capabilities = tool_capabilities(complexity)
                tools = self._tool_registry(
                    question,
                    evidence_bundle,
                    capabilities,
                )
            policy_event_id = trace.record(
                "capability_policy_applied",
                {
                    "requires_external_evidence": complexity.requires_external_evidence,
                    "allowed_tools": {
                        worker: sorted(names) for worker, names in capabilities.items()
                    },
                },
                stage="routing",
                parent_event_id=route_event_id,
            )
            loop = AgentLoop(
                run_llm,
                tools,
                self.config.max_tool_calls,
                max_tokens=self.config.worker_max_tokens,
                max_length_recoveries=self.config.worker_max_length_recoveries,
                max_infrastructure_retries=self.config.worker_max_infrastructure_retries,
                infrastructure_retry_base_delay_seconds=(
                    self.config.worker_infrastructure_retry_base_delay_seconds
                ),
                max_model_rounds=self.config.worker_max_model_rounds,
            )
            by_id = {item.subtask_id: item for item in plan.subtasks}
            tasks: dict[str, asyncio.Task[WorkerResult]] = {}
            semaphore = asyncio.Semaphore(self.config.max_concurrent_workers)

            async def run_subtask(subtask: Subtask) -> WorkerResult:
                parent_id = policy_event_id
                handoffs: list[WorkerHandoff] = []
                if subtask.depends_on:
                    wait_id = trace.record(
                        "dependency_wait",
                        {"subtask_id": subtask.subtask_id, "depends_on": subtask.depends_on},
                        stage="scheduling",
                        agent=subtask.assigned_agent,
                        parent_event_id=policy_event_id,
                    )
                    upstream = await asyncio.gather(*(tasks[key] for key in subtask.depends_on))
                    blocked = []
                    for key, outcome in zip(subtask.depends_on, upstream, strict=True):
                        handoff = build_handoff(by_id[key], outcome, request_spec)
                        if handoff is None:
                            blocked.append(key)
                        else:
                            handoffs.append(handoff)
                    if blocked:
                        blocked_id = trace.record(
                            "dependency_blocked",
                            {
                                "subtask_id": subtask.subtask_id,
                                "blocked_by": blocked,
                                "source_event_ids": [item.trace_event_id for item in upstream],
                            },
                            stage="scheduling",
                            agent=subtask.assigned_agent,
                            parent_event_id=wait_id,
                        )
                        result = WorkerResult(
                            worker=subtask.assigned_agent,
                            subtask_id=subtask.subtask_id,
                            answer="",
                            success=False,
                            generation_status="not_started",
                            failure_reason="dependency_failed",
                            worker_status="dependency_blocked",
                            provider_attempt_count=0,
                        )
                        result.trace_event_id = trace.record(
                            "worker_draft",
                            result.to_dict(),
                            stage="worker",
                            agent=subtask.assigned_agent,
                            parent_event_id=blocked_id,
                        )
                        return result
                    handoff_ids = []
                    for handoff in handoffs:
                        transfers = evidence_transfers(
                            handoff, subtask, request_spec, evidence_bundle,
                        )
                        handoff_id = trace.record(
                            "worker_handoff",
                            {
                                "target_subtask_id": subtask.subtask_id,
                                "wait_event_id": wait_id,
                                "context_risk_count": sum(
                                    context_risk(answer.answer) == "suspicious"
                                    for answer in handoff.request_item_answers
                                ),
                                **handoff.to_dict(),
                                "request_item_ids": list(dict.fromkeys(
                                    t["request_item_id"] for t in transfers)),
                                "origin_request_item_ids": list(dict.fromkeys(
                                    t["origin_request_item_id"] for t in transfers)),
                                "source_event_ids": list(dict.fromkeys(
                                    t["source_event_id"] for t in transfers)),
                                "evidence_links": [{k: t[k] for k in (
                                    "request_item_id", "origin_request_item_id",
                                    "source_event_id", "handoff_event_id",
                                ) if k in t} for t in transfers],
                                "handoff_event_ids": list(dict.fromkeys(
                                    t["handoff_event_id"] for t in transfers
                                    if t.get("handoff_event_id"))),
                            },
                            stage="scheduling",
                            agent=subtask.assigned_agent,
                            parent_event_id=handoff.source_event_id,
                        )
                        handoff_ids.append(handoff_id)
                        evidence_bundle.deliveries.extend(
                            {**t, "handoff_event_id": handoff_id} for t in transfers
                        )
                    parent_id = trace.record(
                        "dependency_ready",
                        {"subtask_id": subtask.subtask_id, "handoff_event_ids": handoff_ids},
                        stage="scheduling",
                        agent=subtask.assigned_agent,
                        parent_event_id=wait_id,
                    )
                try:
                    # Dependencies resolve before occupying an execution slot.
                    async with semaphore:
                        if guard is not None:
                            guard.check()
                        return await asyncio.wait_for(
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
                                parent_event_id=parent_id,
                                evidence_bundle=evidence_bundle,
                                **({"handoffs": tuple(handoffs)} if handoffs else {}),
                            ),
                            timeout=self.config.worker_timeout_seconds,
                        )
                except Exception as outcome:
                    status = (
                        "worker_timeout" if isinstance(outcome, TimeoutError) else "execution_error"
                    )
                    rounds = sum(e.event_type == "worker_model_round"
                                 and e.payload.get("subtask_id") == subtask.subtask_id
                                 for e in trace.events)
                    requests = [e for e in trace.events if e.event_type == "llm_request"
                                and e.payload.get("purpose") in {
                                    f"worker:{subtask.subtask_id}",
                                    f"worker_protocol_recovery:{subtask.subtask_id}",
                                }]
                    task_events = [e for e in trace.events
                                   if e.payload.get("subtask_id") == subtask.subtask_id]
                    result = WorkerResult(
                        worker=subtask.assigned_agent,
                        subtask_id=subtask.subtask_id,
                        answer="",
                        success=False,
                        tool_calls=sum(e.event_type == "tool_call" for e in task_events),
                        generation_status=status,
                        failure_reason=status,
                        worker_status=status,
                        model_round_count=rounds,
                        provider_attempt_count=rounds,
                        infrastructure_retry_count=sum(
                            e.payload.get("recovery_type") == "infrastructure_retry"
                            for e in requests
                        ),
                        length_recovery_count=sum(
                            e.payload.get("recovery_type") == "length_completion_recovery"
                            for e in requests
                        ),
                        protocol_recovery_count=sum(
                            e.stage == "worker_protocol_recovery" for e in requests
                        ),
                        tool_error_count=sum(e.event_type == "tool_result"
                                             and e.payload.get("status") == "tool_error"
                                             for e in task_events),
                    )
                    error_event_id = trace.record(
                        "error",
                        {"where": "worker", "error": type(outcome).__name__},
                        stage="worker",
                        agent=subtask.assigned_agent,
                        parent_event_id=parent_id,
                    )
                    result.trace_event_id = trace.record(
                        "worker_draft",
                        result.to_dict(),
                        stage="worker",
                        agent=subtask.assigned_agent,
                        parent_event_id=error_event_id,
                    )
                    return result

            for subtask in plan.subtasks:
                tasks[subtask.subtask_id] = asyncio.create_task(run_subtask(subtask))
            workers = await asyncio.gather(*(tasks[item.subtask_id] for item in plan.subtasks))
            if guard is not None:
                guard.check()
            execution_failures = [{
                "task_index": index + 1,
                "required_request_items": list(plan.subtasks[index].request_item_ids),
                "worker_status": item.worker_status, "failure_reason": item.failure_reason,
                "model_round_count": item.model_round_count,
                "tool_error_count": item.tool_error_count,
            } for index, item in enumerate(workers) if not item.success]
            trace.record("execution_outcome", {"execution_failures": execution_failures},
                         stage="scheduling")
            async def evaluation_synthesize(*args):
                guard.check()
                return await self._synthesize(*args, llm=run_llm)

            acceptance = await FinalAcceptance(
                self.config, finalizer=self._finalize_answers,
            ).evaluate(
                question=question, description=description, request_spec=request_spec,
                contract=contract, restrictions=restrictions, plan=plan, workers=workers,
                ledger=ledger, evidence_bundle=evidence_bundle, trace=trace, validator=validator,
                semantic=semantic, complexity=complexity, response_profile=response_profile,
                synthesize=self._synthesize if guard is None else evaluation_synthesize,
            )
            if guard is not None:
                guard.check()
            coverage = acceptance.coverage
            request_coverage = acceptance.request_coverage
            final_coverage = acceptance.final_coverage
            final_contract = acceptance.final_contract
            evidence_validation = acceptance.evidence_validation
            semantic = acceptance.semantic
            final_answer = acceptance.final_answer
            status = acceptance.status
            final_check = acceptance.final_check
            if acceptance.preflight_failed:
                failure_reason = acceptance.failure_reason
                trace.record("final_answer", {"answer": ""}, stage="output")
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
                    "final_request_coverage": final_coverage.to_dict(),
                    "final_contract_coverage": final_contract.to_dict(),
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
                    .adapt("", contract, ledger, evidence_bundle, summary,
                           developer if self.config.trace_content_mode == "raw" else {})
                    .to_dict()
                )
                if self.config.trace_content_mode == "metadata":
                    presentation["execution_summary"] = summary
                return {
                    "session_id": session_id,
                    "final_answer": "",
                    "status": "incomplete",
                    "semantic_validation": semantic,
                    "evidence_validation": evidence_validation,
                    "structural_user_request_complete": final_coverage.complete,
                    "structural_contract_complete": final_contract.complete,
                    "user_request_complete": (
                        final_coverage.complete and semantic["complete"] is not False
                    ),
                    "contract_complete": (
                        final_contract.complete and semantic["complete"] is not False
                    ),
                    "missing_required_request_items": final_coverage.missing_request_items,
                    "missing_required_deliverables": final_contract.missing_required_deliverables,
                    "request_item_answers": request_coverage.request_item_answers,
                    "final_request_item_answers": final_coverage.request_item_answers,
                    "successful_workers": coverage.successful_workers,
                    "failed_workers": coverage.failed_workers,
                    "execution_failures": execution_failures,
                    "run_id": trace.run_id,
                    "trace": summary,
                    "presentation": presentation,
                }

            if status == "completed":
                user_added = self.memory.add(session_id, "user", current_input)
                assistant_added = self.memory.add(session_id, "assistant", final_answer)
                trace.record(
                    "memory_write",
                    {
                        "session_id": session_id,
                        "messages_added": int(user_added) + int(assistant_added),
                    },
                    stage="memory",
                )
            trace.record("final_answer", {"answer": final_answer}, stage="output")
            transformed: PresentationTransform | None = None
            if self.presentation_llm is not None and status == "completed":
                presentation_started = perf_counter()
                try:
                    transformed, presentation_response = await transform_presentation(
                        run_presentation_llm, description, question, final_answer, request_spec
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
            if guard is not None:
                guard.check()
            trace.finish(status)
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
                "final_request_coverage": final_coverage.to_dict(),
                "final_contract_coverage": final_contract.to_dict(),
                "tool": {"max_calls_per_worker": self.config.max_tool_calls},
                "retrieval": {
                    "query": evidence_bundle.query,
                    "collection": evidence_bundle.collection,
                    "retrieved_count": len(evidence_bundle.items),
                    "admitted_evidence_ids": [
                        item.evidence_id for item in evidence_bundle.admitted_items
                    ],
                },
                "guardrail": final_check.to_dict(),
                "semantic_validation": semantic,
                "trace_events": [event.to_dict() for event in trace.events],
            }
            presentation = (
                PresentationAdapter()
                .adapt(
                    final_answer, contract, ledger, evidence_bundle, summary,
                    developer if self.config.trace_content_mode == "raw" else {}, transformed
                )
                .to_dict()
            )
            if self.config.trace_content_mode == "metadata":
                presentation["execution_summary"] = summary
            return {
                "session_id": session_id,
                "final_answer": final_answer,
                "status": status,
                "user_request_complete": (
                    final_coverage.complete and semantic["complete"] is not False
                ),
                "contract_complete": final_contract.complete and semantic["complete"] is not False,
                "structural_user_request_complete": final_coverage.complete,
                "structural_contract_complete": final_contract.complete,
                "semantic_validation": semantic,
                "evidence_validation": evidence_validation,
                "missing_required_request_items": final_coverage.missing_request_items,
                "missing_required_deliverables": final_contract.missing_required_deliverables,
                "request_item_answers": request_coverage.request_item_answers,
                "final_request_item_answers": final_coverage.request_item_answers,
                "successful_workers": coverage.successful_workers,
                "failed_workers": coverage.failed_workers,
                "execution_failures": execution_failures,
                "run_id": trace.run_id,
                "trace": summary,
                "presentation": presentation,
            }
        except Exception as caught:
            error = guard.error if guard is not None and guard.error is not None else caught
            if isinstance(error, PlanBudgetError):
                error = StageGenerationError("planning", "subtask_budget_exhausted")
            error_payload = {"error": type(error).__name__}
            if isinstance(error, StageGenerationError):
                error_payload.update(
                    {
                        "failure_stage": error.failure_stage,
                        "failure_reason": error.failure_reason,
                    }
                )
            trace.record("error", error_payload, stage="lifecycle")
            trace.finish("failed")
            if error is caught:
                raise
            raise error from caught

    @staticmethod
    def _finalize_answers(
        answers: dict[str, str], worker_answers: dict[str, str], request_spec: RequestSpec,
        contract: AnswerContract, ledger: EvidenceLedger, trace: TraceRecorder,
    ) -> tuple[dict[str, str], CheckResult, list[str]]:
        # Compatibility shim for existing Python callers and sanitizer injection tests.
        return finalize_answers(
            answers, worker_answers, request_spec, contract, ledger, trace,
            sanitizer=sanitize_answer,
        )

    def _tool_registry(
        self,
        question: str,
        bundle: EvidenceBundle,
        capabilities: dict[str, set[str]],
        *, evaluation_guard: EvaluationRunGuard | None = None,
    ) -> ToolRegistry:
        registry = ToolRegistry(self.config.max_tool_calls, capabilities,
                                evidence_requirements=bundle.evidence_requirements)
        collection_router = CollectionRouter(
            RetrievalConfig(
                generic_collection=self.config.generic_collection,
                special_collection=self.config.special_collection,
                top_k=self.config.retrieval_top_k,
                admission_threshold=self.config.retrieval_threshold,
            )
        )

        async def retrieve(
            context: ToolExecutionContext | None, **arguments: Any
        ) -> dict[str, Any]:
            tool_name = str(arguments.pop("_tool_name", "search_knowledge"))
            tool_input = " ".join(str(value) for value in arguments.values())
            query = QueryBuilder().build(
                question,
                context.description if context else "",
                context.deliverable_ids if context else (),
                tool_input,
                request_items=context.request_items if context else (),
                excluded_source_spans=context.excluded_source_spans if context else (),
                case_description=bundle.case_description,
            )
            decision = collection_router.route(tool_name)
            trace_context = {
                "retrieval_id": str(uuid4()),
                "subtask_id": context.subtask_id if context else None,
                "deliverable_ids": list(context.deliverable_ids) if context else [],
                "request_item_ids": [item.id for item in context.request_items]
                if context
                else [],
                "query": query,
                "collection": decision.collection,
                "routing_reason": decision.reason,
                "top_k": self.config.retrieval_top_k,
            }
            if evaluation_guard is not None:
                evaluation_guard.check()
            try:
                items = await self.retrieval_backend.search(
                    query, decision.collection, self.config.retrieval_top_k
                )
            except Exception as error:
                if evaluation_guard is not None:
                    evaluation_guard.check()
                bundle.retrievals.append({**trace_context, "tool": tool_name,
                    "status": "disabled" if isinstance(error, RetrievalDisabledError)
                    else "retrieval_error", "admitted_evidence_ids": [],
                    "error_type": type(error).__name__})
                raise _RetrievalToolError(error, trace_context) from error
            if evaluation_guard is not None:
                evaluation_guard.check()
            # A search may reuse backend objects. Snapshot before admission so later
            # calls cannot rewrite an earlier source's content or admission state.
            items = deepcopy(items)
            for item in items:
                item.admitted = False
                item.admission_reason = "not_evaluated"
                if contains_instruction_spoof(item.to_dict()):
                    item.admission_reason = "instruction_spoof_quarantined"
            admit_evidence(items, self.config.retrieval_threshold, self.config.retrieval_top_k)
            # The same ID must never name different source content within a run.
            known_items = {item.evidence_id: item for item in bundle.items}
            for item in items:
                previous = known_items.get(item.evidence_id)
                if previous and (any(getattr(previous, k) != getattr(item, k) for k in (
                    "document_id", "source_id", "source", "url", "title", "section", "text",
                )) or any(previous.metadata.get(k) != item.metadata.get(k) for k in (
                    "source_kind", "document_type", "publication_type", "type",
                ))):
                    item.admitted = False
                    item.admission_reason = "conflicting_evidence_id"
            bundle.query = query
            bundle.collection = decision.collection
            known = {item.evidence_id for item in bundle.items}
            for item in items:
                if item.evidence_id not in known:
                    bundle.items.append(item)
                    known.add(item.evidence_id)
                elif item.admitted:
                    # A later good hit can supersede an earlier rejected score for
                    # the same source, without rewriting the per-search records.
                    for index, previous in enumerate(bundle.items):
                        if previous.evidence_id == item.evidence_id:
                            if not previous.admitted or item.score > previous.score:
                                bundle.items[index] = item
                            break
            compact = EvidenceBundle(query, decision.collection, items).compact()
            by_id = {item.evidence_id: item for item in items if item.admitted}
            for entry in compact:
                entry["source_kind"] = declared_source_kind(by_id[entry["evidence_id"]], tool_name)
            bundle.retrievals.append({**trace_context, "tool": tool_name,
                "status": "satisfied" if compact else "low_quality" if items else "no_results",
                "admitted_evidence_ids": [item["evidence_id"] for item in compact]})
            return {
                "query": query,
                "evidence_status": (
                    "relevant_evidence_admitted" if compact else "no_relevant_evidence"
                ),
                "admitted": compact,
                "_trace_retrieval": {
                    **trace_context,
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
                    "quarantined_count": sum(
                        item.admission_reason == "instruction_spoof_quarantined" for item in items
                    ),
                    "context_risk_count": sum(
                        context_risk(item.to_dict()) == "suspicious" for item in items
                    ),
                },
            }

        for name in RETRIEVAL_TOOLS:

            async def handler(
                context: ToolExecutionContext | None, _name: str = name, **kwargs: Any
            ) -> dict[str, Any]:
                return await retrieve(context, _tool_name=_name, **kwargs)

            registry.register(name, handler, with_context=True)
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
        *, llm: LLMClient | None = None,
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
                    "Return exactly one JSON object with an answers array. Each entry must "
                    "contain exactly request_item_id and answer (a non-empty string). Include "
                    "each required RequestSpec ID exactly once; do not invent IDs. Integrate "
                    "the worker drafts within each answer, using natural prose or Markdown, "
                    "with substantive content rather than empty headings. "
                    "Produce the smallest complete answer that satisfies every required item in "
                    "the RequestSpec and the AnswerContract. Preserve an answer for every required "
                    "request_item_id; do not omit one during synthesis. "
                    "Answer requested deliverables and must_cover items first. "
                    "Preserve [EVIDENCE:<evidence_id>] citations inside their corresponding "
                    "answers; never invent or transfer a citation from an unrelated answer. "
                    "Remove repetition and unrelated worker expansion; "
                    "do not add new deliverables. "
                    "Preserve only "
                    "the rationale needed for the medical conclusion and necessary safety "
                    "warnings. Do not add patient facts or hidden reasoning.\n\n"
                    "Public procedural skill (synthesis):\n"
                    f"{self.skills['synthesizer'].instructions}"
                    + CONTEXT_BOUNDARY
                ),
            },
            {
                "role": "user",
                "content": "Synthesize this payload:\n" + json.dumps(payload, ensure_ascii=False),
            },
        ]
        generation = await run_with_length_recovery(
            self.llm if llm is None else llm,
            policy=GenerationPolicy(
                self.config.synthesis_max_tokens,
                self.config.synthesis_max_length_recoveries,
            ),
            stage="synthesis",
            agent="synthesizer",
            messages=messages,
            response_format={"type": "json_object"},
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
        return response.content

    async def close(self) -> None:
        if not self._closed:
            if (self.validation_llm is not self.llm
                    and self.validation_llm is not self.presentation_llm):
                await self.validation_llm.close()
            if self.presentation_llm is not None and self.presentation_llm is not self.llm:
                await self.presentation_llm.close()
            await self.llm.close()
            retrieval_close = getattr(self.retrieval_backend, "close", None)
            if retrieval_close is not None:
                result = retrieval_close()
                if inspect.isawaitable(result):
                    await result
            self._closed = True
