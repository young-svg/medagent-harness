"""Five-case offline architecture adapters. All completion gates are shared."""

from __future__ import annotations

import asyncio
import json
import subprocess
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from medagent.agents.base import AgentDefinition, WorkerResult
from medagent.context.evidence_ledger import build_evidence_ledger
from medagent.observability.replay import read_trace
from medagent.observability.tracer import TraceRecorder
from medagent.planning.complexity import build_complexity_profile, build_response_profile
from medagent.planning.models import Plan, Subtask
from medagent.planning.planner import Planner, apply_contract_policy
from medagent.retrieval.evidence import EvidenceBundle
from medagent.runtime.acceptance import FinalAcceptance
from medagent.runtime.agent_loop import AgentLoop
from medagent.runtime.config import RuntimeConfig
from medagent.runtime.evaluation_guard import EvaluationRunGuard
from medagent.runtime.evaluation_inputs import content_sha256
from medagent.runtime.handoff import build_handoff, evidence_transfers
from medagent.runtime.native_engine import NativeMedAgentEngine
from medagent.runtime.semantic_validation import SemanticValidator
from medagent.skills.loader import ProceduralSkill

from .budget import MeteredLLM, MeteredTools, RunBudget
from .data import ROOT, FrozenCorpusBackend, dataset_sha, frozen_request, load_case, seeded_memory
from .scripted import SmokeLLM

GENERAL = AgentDefinition(
    "diagnostic_agent",
    "General evaluation executor",
    "All assigned tasks, preserving request scope.",
    "No invented facts, sources or performed actions.",
)
SPEC_PATH = Path(__file__).with_name("general_executor.md")
GENERAL_SPEC = ProceduralSkill(
    "w7_general_executor", SPEC_PATH.read_text(encoding="utf-8"), SPEC_PATH
)


class EvaluationEngine(NativeMedAgentEngine):
    """Evaluation-only instrumentation at the existing registry factory seam."""

    def __init__(self, *args, budget, common_tools, **kwargs):
        self.budget, self.common_tools = budget, common_tools
        super().__init__(*args, **kwargs)

    def _tool_registry(self, question, bundle, capabilities, **kwargs):
        # Equal capability envelope, always intersected by the original role whitelist
        # and per-RQ declaration. Never grant a role a new tool.
        filtered = {role: set(self.common_tools) for role in capabilities}
        registry = super()._tool_registry(question, bundle, filtered, **kwargs)
        self.metered_tools = MeteredTools(registry, self.budget)
        return self.metered_tools

    def _evaluation_conditions(self, history, tools):
        return {
            **super()._evaluation_conditions(history, tools),
            "runner": {
                "dataset_sha256": dataset_sha(),
                "selector_sha256": sha256(
                    (ROOT / "validate_candidates.py").read_bytes()
                ).hexdigest(),
                "selector_configuration": {
                    "enabled": self.retrieval_backend.enabled,
                    "fault": self.retrieval_backend.fault,
                    "case_id": self.retrieval_backend.case_id,
                    "document_sha256": sha256(
                        json.dumps(
                            self.retrieval_backend.document, sort_keys=True, ensure_ascii=False
                        ).encode()
                    ).hexdigest(),
                },
                "general_executor_sha256": GENERAL_SPEC.sha256,
                "common_tool_envelope": sorted(self.common_tools),
                "global_limits": {
                    "workers": self.budget.worker_limit,
                    "models": self.budget.model_limit,
                    "tools": self.budget.tool_limit,
                },
            },
        }


async def single_executor(engine, request, case, architecture, primary, validation):
    """A: one full-task ReAct. B: actual Planner/Policy then one sequential executor."""
    spec, contract, restrictions = request.materialize(request.description, request.question)
    config = engine.config
    trace = TraceRecorder(config.trace_dir, secrets=(config.llm_api_key,))
    trace.record("run_start", {"mode": "offline_evaluation"}, stage="lifecycle")
    history = engine.memory.context_with_metadata(
        case["session_layout"]["active_session"],
        request.description + "\nQuestion: " + request.question,
    )[0][:-1]
    current = {"description": request.description, "question": request.question}
    declarations = request.evidence_requirements
    current["external_evidence_requirements"] = json.dumps([r.to_dict() for r in declarations])
    complexity = build_complexity_profile(
        request.question, contract, spec, frozen_evidence_requirements=declarations
    )
    profile = build_response_profile(complexity)
    ledger = build_evidence_ledger(request.description)
    bundle = EvidenceBundle("", "", [])
    bundle.case_description, bundle.evidence_requirements = request.description, declarations
    guard = EvaluationRunGuard()
    guard.watch("model_configuration_drift", engine._evaluation_models)
    guard.watch("retrieval_configuration_drift", engine._evaluation_retrieval)
    tools = engine._tool_registry(
        request.question,
        bundle,
        {
            r: set(engine.common_tools)
            for r in ("diagnostic_agent", "consultation_agent", "research_agent")
        },
        evaluation_guard=guard,
    )
    guard.watch(
        "evaluation_conditions_drift", lambda: engine._evaluation_conditions(history, tools)
    )
    primary = guard.wrap(
        primary, temperature=config.llm_temperature, max_tokens=config.llm_max_tokens
    )
    validation = guard.wrap(
        validation, temperature=config.llm_temperature, max_tokens=config.llm_max_tokens
    )
    validator = SemanticValidator(validation, config, trace, memory_context=history)
    validator.require_calls(1)
    policy_plan = None
    if architecture == "A":
        plan = Plan(
            [
                Subtask(
                    "react",
                    request.question,
                    GENERAL.agent_id,
                    contract.requested_deliverables[:],
                    request_item_ids=[i.id for i in spec.items],
                )
            ]
        )
    else:
        policy_plan = apply_contract_policy(
            await Planner(
                primary,
                max_tokens=config.planner_max_tokens,
                max_length_recoveries=config.planner_max_length_recoveries,
                max_subtasks=config.max_subtasks,
            ).plan(
                request.question,
                contract,
                ledger,
                trace,
                current_context=current,
                memory_context=history,
                complexity=complexity,
                request_spec=spec,
            ),
            contract,
            complexity,
            spec,
        )
        guard.check()
        # B deliberately has ONE general executor, not specialized roles or Native DAG scheduling.
        plan = replace(
            policy_plan,
            subtasks=[replace(t, assigned_agent=GENERAL.agent_id) for t in policy_plan.subtasks],
        )
    plan.validate()
    plan.validate_budget(config.max_subtasks)
    plan_event = trace.record("plan_created", plan.to_dict(), stage="planning")
    loop = AgentLoop(
        primary,
        tools,
        config.max_tool_calls,
        max_tokens=config.worker_max_tokens,
        max_length_recoveries=config.worker_max_length_recoveries,
        max_infrastructure_retries=config.worker_max_infrastructure_retries,
        infrastructure_retry_base_delay_seconds=config.worker_infrastructure_retry_base_delay_seconds,
        max_model_rounds=config.worker_max_model_rounds,
    )
    completed, remaining = {}, list(plan.subtasks)
    while remaining:
        guard.check()
        task = next(t for t in remaining if all(d in completed for d in t.depends_on))
        remaining.remove(task)
        handoffs = [
            build_handoff(next(t for t in plan.subtasks if t.subtask_id == d), completed[d], spec)
            for d in task.depends_on
        ]
        if any(h is None for h in handoffs):
            event = trace.record(
                "dependency_blocked",
                {"subtask_id": task.subtask_id},
                stage="scheduling",
                parent_event_id=plan_event,
            )
            result = WorkerResult(
                GENERAL.agent_id,
                task.subtask_id,
                "",
                success=False,
                worker_status="dependency_blocked",
                failure_reason="invalid_upstream_handoff",
                trace_event_id=event,
            )
        else:
            parent = plan_event
            for handoff in handoffs:
                transfers = evidence_transfers(handoff, task, spec, bundle)
                parent = trace.record(
                    "worker_handoff",
                    {**handoff.to_dict(), "target_subtask_id": task.subtask_id},
                    stage="scheduling",
                    parent_event_id=handoff.source_event_id,
                )
                bundle.deliveries.extend({**t, "handoff_event_id": parent} for t in transfers)
            # Separate execution context per task; one at a time, not a pool of Workers.
            result = await asyncio.wait_for(
                loop.execute(
                    GENERAL,
                    task,
                    spec,
                    contract,
                    ledger,
                    current,
                    history,
                    GENERAL_SPEC,
                    trace,
                    complexity,
                    profile,
                    parent_event_id=parent,
                    handoffs=tuple(handoffs),
                    evidence_bundle=bundle,
                ),
                timeout=config.worker_timeout_seconds,
            )
        completed[task.subtask_id] = result
    workers = [completed[t.subtask_id] for t in plan.subtasks]
    acceptance = await FinalAcceptance(config).evaluate(
        question=request.question,
        description=request.description,
        request_spec=spec,
        contract=contract,
        restrictions=restrictions,
        plan=plan,
        workers=workers,
        ledger=ledger,
        evidence_bundle=bundle,
        trace=trace,
        validator=validator,
        semantic={"status": "not_run", "complete": False, "calls": 0, "items": []},
        complexity=complexity,
        response_profile=profile,
        # No extra final-answer Agent for A/B; shared service merges per-RQ outputs.
    )
    guard.check()
    trace.finish(acceptance.status)
    result = {
        "run_id": trace.run_id,
        "status": acceptance.status,
        "semantic_validation": acceptance.semantic,
        "evidence_validation": acceptance.evidence_validation,
        "final_request_item_answers": acceptance.final_coverage.request_item_answers,
        "final_request_coverage": acceptance.final_coverage.to_dict(),
        "final_contract_coverage": acceptance.final_contract.to_dict(),
        "guardrail": acceptance.final_check.to_dict() if acceptance.final_check else None,
        "workers": [w.to_dict() for w in workers],
        "plan": plan.to_dict(),
        "policy_plan": policy_plan.to_dict() if policy_plan else None,
    }
    return result


def execution_audit(architecture, plan, models, worker_inputs):
    """Observed model intervals plus actual structured Handoff inputs, not a quality score."""
    if architecture == "A":
        return {
            "dependency_check": "NOT_APPLICABLE_SINGLE_REACT",
            "violations": [],
            "note": "Within-one-Agent semantic step order requires independent review.",
        }
    violations = []
    worker_models = [m for m in models if m["stage"] == "worker" and m["task_id"]]
    for task in plan.get("subtasks", []):
        starts = [m["start_ms"] for m in worker_models if m["task_id"] == task["subtask_id"]]
        if not starts:
            continue  # A correctly blocked task has no model attempt.
        for upstream in task["depends_on"]:
            ends = [m["end_ms"] for m in worker_models if m["task_id"] == upstream]
            input_rows = [p for p in worker_inputs if p["task_id"] == task["subtask_id"]]
            if (
                not ends
                or max(ends) > min(starts)
                or not input_rows
                or upstream not in input_rows[0]["upstream_task_ids"]
            ):
                violations.append(
                    {"upstream_task_id": upstream, "target_task_id": task["subtask_id"]}
                )
    return {
        "dependency_check": "OBSERVED_MODEL_INTERVALS_AND_HANDOFF_INPUTS",
        "violations": violations,
    }


def independent_score(case, result, *, reviewer=None, grade=None, per_item=None, rationale=None):
    """Explicit human scoring entry; internal completed is never a grade."""
    if reviewer is None:
        return {
            "status": "UNSCORED",
            "grade": None,
            "required_request_item_ids": case["must_request_item_ids"],
            "rubric_origin": "frozen_v1/cases.json",
            "internal_status": result["status"],
        }
    if (
        not reviewer.strip()
        or grade not in {"PASS", "PARTIAL", "FAIL"}
        or not rationale
        or set(per_item or {}) != set(case["must_request_item_ids"])
        or any(
            not isinstance(value, str) or value not in case["completion_rubric"]["per_item"]
            for value in (per_item or {}).values()
        )
    ):
        raise ValueError("incomplete_independent_review")
    return {
        "status": "REVIEWED",
        "reviewer": reviewer,
        "grade": grade,
        "per_item": per_item,
        "rationale": rationale,
    }


async def run_case(
    case_id,
    architecture,
    output: Path,
    *,
    memory_on=True,
    rag_on=True,
    fault=None,
    replay=None,
    shared_plan_cost=None,
    limits=None,
    mode="scripted",
    batch=None,
):
    if architecture not in {"A", "B", "C", "C-serial"}:
        raise ValueError("unknown_architecture")
    if architecture == "C-serial" and replay is None:
        raise ValueError("serial_requires_actual_C_plan")
    if mode not in {"scripted", "live"}:
        raise ValueError("mode_must_be_explicit_scripted_or_live")
    if mode == "live" and (batch is None or fault is not None):
        raise ValueError("live_requires_batch_and_no_scripted_faults")
    case = load_case(case_id)
    request = frozen_request(case)
    budget = RunBudget(**(limits or {}), batch=batch)
    trace_root = output / "traces" / str(uuid4())
    config = RuntimeConfig(
        trace_dir=str(trace_root),
        max_concurrent_workers=1 if architecture == "C-serial" else 4,
        worker_infrastructure_retry_base_delay_seconds=0,
    )
    if mode == "live":
        from .live import live_config, make_client

        config = replace(
            live_config(),
            trace_dir=str(trace_root),
            max_concurrent_workers=1 if architecture == "C-serial" else 4,
        )
        primary_client, validation_client = make_client(config, batch), make_client(config, batch)
    else:
        primary_client = SmokeLLM(case, fault=fault)
        validation_client = SmokeLLM(case, validation=True)
    origin = "provider_reported" if mode == "live" else "scripted_simulation"
    primary = MeteredLLM(primary_client, budget, usage_origin=origin, allow_recovery=mode != "live")
    validation = MeteredLLM(
        validation_client,
        budget,
        validation=True,
        usage_origin=origin,
        allow_recovery=mode != "live",
    )
    backend = FrozenCorpusBackend(case_id, fault=fault, enabled=rag_on)
    engine = EvaluationEngine(
        config,
        llm=primary,
        validation_llm=validation,
        retrieval_backend=backend,
        memory=seeded_memory(case, memory_on),
        budget=budget,
        common_tools={"disease_code", "search_knowledge"} if case_id == "E03" else set(),
    )
    started = perf_counter()
    exported = None
    try:
        if architecture in {"C", "C-serial"}:
            run = await engine.analyze_frozen(
                request, case["session_layout"]["active_session"], plan=replay
            )
            result, exported = run.result, run.plan
        else:
            result = await single_executor(engine, request, case, architecture, primary, validation)
    except Exception as error:
        # A failed run remains in the denominator with all prior attempts/unknown usage.
        trace_ids = sorted(trace_root.glob("*/trace.jsonl"))
        result = {
            "status": "error",
            "error_type": type(error).__name__,
            "run_id": trace_ids[-1].parent.name if trace_ids else None,
        }
        if trace_ids:
            # Every run_case gets a fresh directory below, so this cannot select a different run.
            with trace_ids[-1].open("a", encoding="utf-8") as stream:
                from medagent.observability.schema import TraceEvent

                terminal = TraceEvent(
                    run_id=result["run_id"],
                    stage="evaluation",
                    event_type="error",
                    payload={"status": "error", "error_type": type(error).__name__},
                )
                stream.write(json.dumps(terminal.to_dict()) + "\n")
                end = TraceEvent(
                    run_id=result["run_id"],
                    stage="evaluation",
                    event_type="run_end",
                    parent_event_id=terminal.event_id,
                    payload={"status": "error"},
                )
                stream.write(json.dumps(end.to_dict()) + "\n")
    finally:
        await engine.close()
    elapsed = round((perf_counter() - started) * 1000, 3)
    run_id = result.get("run_id")
    events = read_trace(trace_root / run_id) if run_id else []
    workers = result.get("workers", [])
    if not workers:
        workers = result.get("developer", {}).get("workers", [])
    if not workers:
        workers = [e["payload"] for e in events if e["event_type"] == "worker_draft"]
    effective_plan = exported.materialize().to_dict() if exported else result.get("plan", {})
    summary = budget.summary()
    record = {
        "architecture": architecture,
        "case_id": case_id,
        "run_id": run_id,
        "dataset_sha256": dataset_sha(),
        "request_sha256": request.sha256,
        "code_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "runner_source_state": "uncommitted",
        "model_mode": "LIVE_PROVIDER" if mode == "live" else "SCRIPTED_OFFLINE_ONLY",
        "presentation_llm_enabled": engine.presentation_llm is not None,
        "plan_sha256": exported.sha256 if exported else None,
        "plan_source": "frozen_replay"
        if replay
        else "planner_policy"
        if architecture != "A"
        else "no_planner",
        "conditions_sha256": exported.conditions_sha256 if exported else None,
        "memory_on": memory_on,
        "rag_on": rag_on,
        "fault": fault,
        "native_status": result["status"],
        "status": "budget_exhausted" if budget.denied else result["status"],
        "latency_ms": elapsed,
        "metrics": summary,
        "models": budget.models,
        "tools": budget.tools,
        "retrievals": backend.requests,
        "result": result,
        "independent_score": independent_score(case, result),
        "shared_plan_generation_cost": shared_plan_cost,
        "acceptance_implementation": "medagent.runtime.acceptance.FinalAcceptance.evaluate",
        "acceptance_observed": any(e["event_type"] == "contract_coverage" for e in events),
        "trace_path": str((trace_root / run_id / "trace.jsonl").relative_to(output))
        if run_id
        else None,
        "worker_inputs": [
            {
                "task_id": p["subtask"]["subtask_id"],
                "permission_identity": "diagnostic_agent"
                if architecture in {"A", "B"}
                else next(
                    (
                        t["assigned_agent"]
                        for t in effective_plan.get("subtasks", [])
                        if t["subtask_id"] == p["subtask"]["subtask_id"]
                    ),
                    None,
                ),
                "request_item_ids": p["subtask"]["request_item_ids"],
                "history_messages": len(p["bounded_session_context"]),
                "upstream_task_ids": [
                    h["source_subtask_id"] for h in p.get("upstream_handoffs", [])
                ],
            }
            for p in primary.worker_payloads
        ],
        "recovery_events": [
            {"event_type": e["event_type"], "payload": e["payload"]}
            for e in events
            if "recovery" in e["event_type"]
            or "retry" in e["event_type"]
            or (
                e["event_type"] == "generation_summary"
                and e["payload"].get("length_recovery_count", 0)
            )
        ],
    }
    record["execution_plan"] = effective_plan
    record["execution_layout_sha256"] = content_sha256(effective_plan) if effective_plan else None
    if architecture == "B" and result.get("policy_plan"):
        record["plan_sha256"] = content_sha256(result["policy_plan"])
    record["worker_counts"] = {
        "success": sum(w.get("success", False) for w in workers),
        "failed": sum(not w.get("success", False) for w in workers),
        "blocked": sum(w.get("worker_status") == "dependency_blocked" for w in workers),
        "execution_contexts_with_model_attempts": len(
            {m["task_id"] for m in budget.models if m["stage"] == "worker" and m["task_id"]}
        ),
        "execution_role_count": len({p["permission_identity"] for p in record["worker_inputs"]}),
    }
    record["dag_audit"] = execution_audit(
        architecture, effective_plan, budget.models, record["worker_inputs"]
    )
    record["rq_acceptance"] = result.get("semantic_validation", {}).get("items", [])
    record["recovery_summary"] = {
        "length_recoveries": sum(
            e["payload"].get("length_recovery_count", 0)
            for e in events
            if e["event_type"] == "generation_summary"
        ),
        "infrastructure_retries": sum(
            e["event_type"] == "worker_infrastructure_retry_scheduled" for e in events
        ),
        "protocol_repairs": sum(
            e["event_type"] == "worker_protocol_recovery_start" for e in events
        ),
        "successful_protocol_repairs": sum(
            e["event_type"] == "worker_protocol_recovery_result"
            and e["payload"].get("success", False)
            for e in events
        ),
    }
    record["guardrail_checks"] = [
        e["payload"] for e in events if e["event_type"] == "checker_result"
    ]
    record["run_cost_views"] = {
        "actual_model_attempts": summary["model_attempts"],
        "execution_model_attempts_excluding_planning": summary["model_attempts"]
        - summary["by_stage"].get("planning", 0),
        "full_architecture_attributed_model_attempts": summary["model_attempts"]
        + ((shared_plan_cost or {}).get("model_attempts", 0) if architecture == "C-serial" else 0),
        "money": "UNKNOWN" if mode == "live" else "NOT_MEASURED_NO_PROVIDER_CALLS",
    }
    if mode == "live":
        if any(w.get("worker_status") in {"protocol_error", "budget_exhausted"} for w in workers):
            batch.stop("worker_protocol_or_budget_failure")
        record["batch_stop_reason"] = batch.stop_reason
        if batch.stop_reason:
            record["status"] = "stopped"
    if budget.denied and run_id:
        # Supplement Native's classification with a safe evaluation-side terminal.
        from medagent.observability.schema import TraceEvent

        event = TraceEvent(
            run_id=run_id,
            stage="evaluation",
            event_type="error",
            payload={"status": "budget_exhausted", "failure_reason": budget.denied[0]},
        )
        with (trace_root / run_id / "trace.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event.to_dict()) + "\n")
            end = TraceEvent(
                run_id=run_id,
                stage="evaluation",
                event_type="run_end",
                parent_event_id=event.event_id,
                payload={"status": "budget_exhausted"},
            )
            stream.write(json.dumps(end.to_dict()) + "\n")
    # Save exact plan envelope; metadata trace alone intentionally omits task text/IDs.
    if exported:
        record["frozen_plan"] = exported.to_dict()
    record["runner_sources_sha256"] = {
        p.name: sha256(p.read_bytes()).hexdigest()
        for p in sorted(Path(__file__).parent.iterdir())
        if p.suffix in {".py", ".md"}
    }
    output.mkdir(parents=True, exist_ok=True)
    name = f"{case_id}-{architecture}-memory{int(memory_on)}-{fault or 'normal'}-{run_id}"
    if mode == "live":
        from medagent.observability.tracer import redact_trace_value

        record = json.loads(
            json.dumps(redact_trace_value(record), ensure_ascii=False).replace(
                config.llm_api_key, "[REDACTED]"
            )
        )
    (output / f"{name}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return record, exported
