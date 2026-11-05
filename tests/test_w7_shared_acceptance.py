"""Small real execution harnesses, not completed four-group evaluation adapters."""
from __future__ import annotations

import json

import pytest

from medagent.context.evidence_ledger import build_evidence_ledger
from medagent.context.request_spec import RequestItem, RequestSpec
from medagent.guardrails.contract_checker import CheckResult, Guardrail
from medagent.llm import ScriptedLLM
from medagent.observability.replay import read_trace
from medagent.observability.tracer import TraceRecorder
from medagent.planning.complexity import build_complexity_profile, build_response_profile
from medagent.planning.models import Plan, Subtask
from medagent.planning.planner import Planner, apply_contract_policy
from medagent.retrieval.backend import OffRetrievalBackend
from medagent.retrieval.evidence import EvidenceBundle
from medagent.runtime.acceptance import FinalAcceptance
from medagent.runtime.agent_loop import AgentLoop
from medagent.runtime.config import RuntimeConfig
from medagent.runtime.evaluation_inputs import FrozenRequest
from medagent.runtime.handoff import build_handoff
from medagent.runtime.native_engine import AGENTS, NativeMedAgentEngine
from medagent.runtime.semantic_validation import SemanticValidator, contract_for_spec
from tests.test_semantic_validation import verdict
from tests.test_w7_evaluation_interfaces import (
    ANSWERS,
    DESCRIPTION,
    QUESTION,
    answer,
    combined,
    plan_dict,
)


def scenario_inputs(scenario):
    question = "Explain Alpha."
    items = [RequestItem("RQ1", question, True, 1, question, "UNKNOWN")]
    response = answer(1)
    check = verdict(ANSWERS[:1])
    if scenario in {"incomplete", "two_step"}:
        question = QUESTION
        items.append(RequestItem("RQ2", "Explain Beta.", True, 2, "Explain Beta.", "UNKNOWN"))
        if scenario == "two_step":
            response, check = combined(), verdict(ANSWERS)
    elif scenario == "optional":
        question = "Explain Alpha. Optional: discuss Beta."
        items.append(RequestItem("RQ2", "discuss Beta.", False, 2,
                                 "Optional: discuss Beta.", "UNKNOWN"))
        check = verdict([ANSWERS[0], ""], ["satisfied", "unanswered"])
    elif scenario == "w2_failure":
        check = verdict(ANSWERS[:1], ["off_topic"])
    elif scenario == "evidence":
        question = "Consult external materials and identify the sources."
        items = [RequestItem("RQ1", question, True, 1, question, "UNKNOWN")]
    elif scenario == "restrictions":
        question = "Explain Alpha. Do not provide treatment."
        check = verdict(ANSWERS[:1], restrictions=["satisfied"])
    spec = RequestSpec(items)
    restrictions = ["Do not provide treatment."] if scenario == "restrictions" else []
    contract = contract_for_spec(spec, restrictions)
    request = FrozenRequest.capture(DESCRIPTION, question, spec, contract, restrictions)
    return request, response, check


async def run_shape(tmp_path, group, scenario):
    """A calls AgentLoop; B calls Planner then a sequential loop; C calls Native."""
    request, response, check = scenario_inputs(scenario)
    spec, contract, restrictions = request.materialize(DESCRIPTION, request.question)
    if scenario == "two_step" and group != "A":
        planned = plan_dict()
        if group == "B":
            for task in planned["subtasks"]:
                task["assigned_agent"] = "diagnostic_agent"
        responses = [planned, answer(1), answer(2)] + ([combined()] if group == "C" else [])
    else:
        planned = plan_dict(False)
        planned["subtasks"][0]["request_item_ids"] = [item.id for item in spec.items]
        responses = ([planned] if group != "A" else []) + [response]
    llm, validation = ScriptedLLM(responses), ScriptedLLM([check])
    config = RuntimeConfig(trace_dir=str(tmp_path))
    engine = NativeMedAgentEngine(config, llm=llm, validation_llm=validation,
                                  retrieval_backend=OffRetrievalBackend())
    try:
        if group == "C":
            run = await engine.analyze_frozen(request)
            events = read_trace(tmp_path / run.result["run_id"])
            return run.result, llm, validation, events
        trace = TraceRecorder(tmp_path)
        ledger = build_evidence_ledger(DESCRIPTION)
        complexity = build_complexity_profile(request.question, contract, spec)
        profile = build_response_profile(complexity)
        if group == "A":
            plan = Plan([Subtask("single", request.question, "diagnostic_agent",
                                 list(contract.requested_deliverables),
                                 request_item_ids=[item.id for item in spec.items])])
        else:
            plan = await Planner(llm).plan(request.question, contract, ledger, trace,
                                           complexity=complexity, request_spec=spec)
            plan = apply_contract_policy(plan, contract, complexity, spec)
        from medagent.planning.complexity import tool_capabilities
        bundle = EvidenceBundle("", "", [])
        bundle.case_description = DESCRIPTION
        tools = engine._tool_registry(request.question, bundle, tool_capabilities(complexity))
        loop = AgentLoop(llm, tools)
        workers = []
        for task in plan.subtasks:
            upstream = tuple(build_handoff(source, worker, spec)
                             for source, worker in zip(plan.subtasks, workers, strict=False)
                             if source.subtask_id in task.depends_on)
            assert all(h is not None for h in upstream)
            workers.append(await loop.execute(
                AGENTS[task.assigned_agent], task, spec, contract, ledger,
                {"description": DESCRIPTION, "question": request.question}, [],
                engine.skills[task.assigned_agent], trace, complexity, profile,
                handoffs=upstream, evidence_bundle=bundle,
            ))
        validator = SemanticValidator(validation, config, trace)
        accepted = await FinalAcceptance(config).evaluate(
            question=request.question, description=DESCRIPTION, request_spec=spec,
            contract=contract, restrictions=restrictions, plan=plan, workers=workers,
            ledger=ledger, evidence_bundle=bundle, trace=trace, validator=validator,
            semantic={"status": "disabled", "complete": None, "calls": 0,
                      "items": [], "assurance": "structural_only"},
            complexity=complexity, response_profile=profile,
        )
        trace.finish(accepted.status)
        return {"status": accepted.status, "final_request_item_answers":
                accepted.final_coverage.request_item_answers,
                "semantic_validation": accepted.semantic,
                "evidence_validation": accepted.evidence_validation}, llm, validation, read_trace(
                    trace.run_dir)
    finally:
        await engine.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["completed", "incomplete", "hard_block", "evidence",
                                      "w2_failure", "optional", "two_step", "restrictions"])
async def test_real_a_b_c_execution_paths_use_identical_final_gates(
    tmp_path, monkeypatch, scenario,
):
    if scenario == "hard_block":
        monkeypatch.setattr(Guardrail, "check", lambda *a: CheckResult(
            False, blocking_issues=["synthetic_hard_block"]))
    outputs = []
    calls = []
    for group in ["A", "B", "C"]:
        result, llm, validator, events = await run_shape(tmp_path / group, group, scenario)
        outputs.append(result)
        calls.append(len(llm.requests))
        assert sum(e["event_type"] == "llm_request" and e["stage"] == "planning"
                   for e in events) == (group != "A")
        if scenario in {"hard_block", "evidence", "incomplete"}:
            assert validator.requests == []
        else:
            assert len(validator.requests) == 1
        if scenario == "restrictions":
            payload = json.loads(validator.requests[0]["messages"][1]["content"])
            assert payload["restrictions"] == ["Do not provide treatment."]
    expected = ("completed" if scenario in {"completed", "optional", "two_step", "restrictions"}
                else "incomplete")
    assert [o["status"] for o in outputs] == [expected] * 3
    assert all(o["final_request_item_answers"] == outputs[0]["final_request_item_answers"]
               for o in outputs)
    assert all(o["semantic_validation"] == outputs[0]["semantic_validation"] for o in outputs)
    assert all(o["evidence_validation"] == outputs[0]["evidence_validation"] for o in outputs)
    if scenario == "two_step":
        assert calls == [1, 3, 4]  # A; Planner + two B steps; C also synthesizes.
