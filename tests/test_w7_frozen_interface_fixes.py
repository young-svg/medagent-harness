"""Four W7 review fixes: deterministic gold contracts, memory, drift and immutability."""
from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace

import httpx
import pytest

from medagent.context.contract import AnswerContract, build_answer_contract
from medagent.context.models import CoverageItem
from medagent.context.request_spec import RequestItem, RequestSpec
from medagent.llm import OpenAICompatibleLLM, ScriptedLLM
from medagent.memory.session import SessionMemory
from medagent.observability.replay import read_trace
from medagent.planning.complexity import build_complexity_profile, tool_capabilities
from medagent.planning.models import Plan, Subtask
from medagent.retrieval.backend import OffRetrievalBackend
from medagent.retrieval.evidence import EvidenceBundle
from medagent.runtime.agent_loop import AgentLoop
from medagent.runtime.config import RuntimeConfig
from medagent.runtime.evaluation_guard import EvaluationConditionDriftError
from medagent.runtime.evaluation_inputs import (
    FrozenExecutionPlan,
    FrozenRequest,
    canonical_json,
    content_sha256,
)
from medagent.runtime.native_engine import NativeMedAgentEngine
from medagent.runtime.semantic_validation import SemanticValidator
from tests.test_semantic_validation import verdict
from tests.test_w7_evaluation_interfaces import (
    ANSWERS,
    DESCRIPTION,
    answer,
    combined,
    frozen,
    plan_dict,
)


def declared_request(types, *, priorities=None, restrictions=()):
    texts = ["Is surgery indicated?" if t == "SURGERY_INDICATION" else f"Discuss item {i}."
             for i, t in enumerate(types, 1)]
    question = " ".join([*texts, *restrictions])
    required = priorities or [True] * len(types)
    spec = RequestSpec([RequestItem(f"RQ{i}", text, flag, i, text, semantic)
                        for i, (text, semantic, flag) in enumerate(
                            zip(texts, types, required, strict=True), 1)])
    deliverables = list(dict.fromkeys(types))
    must = {t for t, flag in zip(types, required, strict=True) if flag}
    contract = AnswerContract(
        "manual_gold", deliverables,
        must_cover=[t.replace("_", " ").lower() for t in deliverables if t in must],
        optional_cover=[t.replace("_", " ").lower() for t in deliverables if t not in must],
        avoid=[*build_answer_contract("").avoid, *restrictions],
        coverage_checklist=[CoverageItem(t.replace("_", " ").lower(),
                                         "MUST" if t in must else "OPTIONAL")
                            for t in deliverables],
    )
    return question, spec, contract


@pytest.mark.asyncio
async def test_manual_surgery_contract_passes_real_native_frozen_execution(tmp_path):
    question, spec, contract = declared_request(["SURGERY_INDICATION"])
    request = FrozenRequest.capture(DESCRIPTION, question, spec, contract)
    text = "Surgery indication: a provisional discussion, not a confirmed clinical decision."
    planned = {"subtasks": [{"subtask_id": "s", "description": question,
                             "assigned_agent": "consultation_agent", "request_item_ids": ["RQ1"],
                             "deliverable_ids": ["SURGERY_INDICATION"]}]}
    llm = ScriptedLLM([planned, {"answers": [{"request_item_id": "RQ1", "answer": text}]}])
    validation = ScriptedLLM([verdict([text])])
    engine = NativeMedAgentEngine(
        RuntimeConfig(trace_dir=str(tmp_path)), llm=llm,
        validation_llm=validation, retrieval_backend=OffRetrievalBackend(),
    )
    try:
        run = await engine.analyze_frozen(request)
        assert run.result["status"] == "completed"
        worker = json.loads(llm.requests[1]["messages"][1]["content"])
        assert worker["contract"]["requested_deliverables"] == ["SURGERY_INDICATION"]
        assert len(validation.requests) == 1
    finally:
        await engine.close()


@pytest.mark.parametrize("change", ["illegal_type", "prohibition", "priority", "mapping"])
def test_gold_semantic_and_priority_conflicts_are_rejected(change):
    restrictions = ["Do not provide treatment."] if change == "prohibition" else []
    question, spec, contract = declared_request(
        ["DIAGNOSIS_WITH_BASIS", "TREATMENT_PLAN"], priorities=[True, False],
        restrictions=restrictions,
    )
    if change == "illegal_type":
        spec.items[0] = replace(spec.items[0], semantic_type="INVENTED_TYPE")
    elif change == "priority":
        contract.must_cover, contract.optional_cover = contract.optional_cover, contract.must_cover
        contract.coverage_checklist.clear()
    elif change == "mapping":
        contract.requested_deliverables = ["DIAGNOSIS_WITH_BASIS"]
        contract.optional_cover.clear()
        contract.coverage_checklist = contract.coverage_checklist[:1]
    with pytest.raises(ValueError):
        FrozenRequest.capture(DESCRIPTION, question, spec, contract, restrictions)


def test_mixed_prohibition_does_not_keyword_match_an_explicitly_permitted_scope():
    restriction = "Do not provide a diagnosis; only discuss surgery indication."
    question, spec, contract = declared_request(["SURGERY_INDICATION"], restrictions=[restriction])
    request = FrozenRequest.capture(DESCRIPTION, question, spec, contract, [restriction])
    assert request.materialize(DESCRIPTION, question)[1].requested_deliverables == [
        "SURGERY_INDICATION",
    ]


def test_cross_rq_task_deliverables_cannot_be_swapped_even_with_a_new_digest():
    question, spec, contract = declared_request(["DIAGNOSIS_WITH_BASIS", "TREATMENT_PLAN"])
    request = FrozenRequest.capture(DESCRIPTION, question, spec, contract)
    plan = Plan([Subtask(f"t{i}", item.text, "diagnostic_agent",
                         [item.semantic_type], request_item_ids=[item.id])
                 for i, item in enumerate(spec.items, 1)])
    good = FrozenExecutionPlan.capture(plan, request, {})
    good.validate_for(request, {}, 16)
    plan.subtasks[0].deliverable_ids, plan.subtasks[1].deliverable_ids = (
        plan.subtasks[1].deliverable_ids, plan.subtasks[0].deliverable_ids)
    bad = FrozenExecutionPlan.capture(plan, request, {})
    with pytest.raises(ValueError, match="cross-RQ"):
        bad.validate_for(request, {}, 16)


@pytest.mark.parametrize("path", ["constructor", "replace", "capture"])
def test_restriction_lists_are_normalized_and_detached_on_all_construction_paths(path):
    restriction = "Do not provide treatment."
    question, spec, contract = declared_request(["SURGERY_INDICATION"], restrictions=[restriction])
    values = [restriction]
    good = FrozenRequest.capture(DESCRIPTION, question, spec, contract, values)
    if path == "constructor":
        request = FrozenRequest(DESCRIPTION, question, good.spec_json, good.contract_json, values)
    elif path == "replace":
        request = replace(good, restrictions=values)
    else:
        request = good
    before = request.sha256
    values.append("External list mutation")
    assert request.restrictions == (restriction,)
    assert request.sha256 == before
    request.materialize(DESCRIPTION, question)
    with pytest.raises(FrozenInstanceError):
        request.restrictions += ("Mutation",)


def test_plan_nested_mutations_do_not_change_frozen_snapshot_and_tampering_fails():
    request = frozen()
    plan = Plan([Subtask("first", "Explain Alpha", "diagnostic_agent",
                         ["COMPREHENSIVE_CASE_ANALYSIS"], request_item_ids=["RQ1", "RQ2"])])
    snapshot = FrozenExecutionPlan.capture(plan, request, {})
    digest = snapshot.sha256
    plan.subtasks[0].depends_on.append("invented")
    thawed = snapshot.materialize()
    assert thawed.subtasks[0].depends_on == []
    thawed.subtasks[0].depends_on.append("first")
    exported = snapshot.to_dict()
    exported["plan_json"] = canonical_json(thawed.to_dict())
    with pytest.raises(ValueError, match="digest"):
        FrozenExecutionPlan.from_dict(exported)
    assert snapshot.sha256 == digest and snapshot.materialize().subtasks[0].depends_on == []
    with pytest.raises(FrozenInstanceError):
        snapshot.plan_json = "{}"


@pytest.mark.parametrize("field,value", [
    ("description", []), ("question", {}), ("spec_json", {}), ("contract_json", []),
    ("restrictions", [{"text": "not scalar"}]), ("restrictions", "not a sequence of spans"),
])
def test_direct_and_replace_request_reject_mutable_or_invalid_scalar_types(field, value):
    with pytest.raises(ValueError):
        replace(frozen(), **{field: value})


@pytest.mark.parametrize("json_text", ['{"items":[],"items":[]}',
                                        '{"items":[],"extra":NaN}', '[]'])
def test_frozen_json_rejects_duplicates_nonfinite_values_and_nonobjects(json_text):
    with pytest.raises(ValueError):
        replace(frozen(), spec_json=json_text)


def test_request_and_plan_canonicalization_is_identical_for_constructor_and_capture():
    request = frozen()
    pretty = replace(request, spec_json=json.dumps(json.loads(request.spec_json), indent=2),
                     contract_json=json.dumps(json.loads(request.contract_json), indent=2))
    assert pretty.spec_json == request.spec_json and pretty.sha256 == request.sha256
    plan = Plan([Subtask("t", "Explain", "diagnostic_agent",
                         ["COMPREHENSIVE_CASE_ANALYSIS"], request_item_ids=["RQ1", "RQ2"])])
    snapshot = FrozenExecutionPlan.capture(plan, request, {})
    assert replace(snapshot, plan_json=json.dumps(plan.to_dict(), indent=2)) == snapshot
    for field, value in [("plan_json", {}), ("sha256", []), ("request_sha256", "A" * 64),
                          ("conditions_sha256", {"digest": "a" * 64})]:
        with pytest.raises(ValueError):
            replace(snapshot, **{field: value})


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["enabled", "recent_limit", "token_budget", "implementation"])
async def test_actual_empty_memory_configuration_changes_digest_and_rejects_replay(
    tmp_path, change,
):
    class OtherMemory(SessionMemory):
        pass
    request = frozen("Explain Alpha.")
    engine = NativeMedAgentEngine(
        RuntimeConfig(trace_dir=str(tmp_path / "c")), memory=SessionMemory(),
        llm=ScriptedLLM([plan_dict(False), answer(1)]),
        validation_llm=ScriptedLLM([verdict(ANSWERS[:1])]),
        retrieval_backend=OffRetrievalBackend(),
    )
    try:
        capture = await engine.analyze_frozen(request, "session")
    finally:
        await engine.close()
    memory = OtherMemory() if change == "implementation" else SessionMemory(
        enabled=change != "enabled", recent_limit=3 if change == "recent_limit" else 10,
        token_budget=0 if change == "token_budget" else 4096,
    )
    llm = ScriptedLLM([])
    engine = NativeMedAgentEngine(
        RuntimeConfig(trace_dir=str(tmp_path / "serial"), max_concurrent_workers=1), memory=memory,
        llm=llm, validation_llm=ScriptedLLM([]), retrieval_backend=OffRetrievalBackend(),
    )
    try:
        spec, contract, _ = request.materialize(DESCRIPTION, request.question)
        complexity = build_complexity_profile(request.question, contract, spec)
        tools = engine._tool_registry(request.question, EvidenceBundle("", "", []),
                                       tool_capabilities(complexity))
        conditions = engine._evaluation_conditions([], tools)
        assert conditions["memory"]["history"] == []
        assert capture.plan.conditions_sha256 != content_sha256(conditions)
        with pytest.raises(ValueError, match="conditions mismatch"):
            await engine.analyze_frozen(request, "session", plan=capture.plan)
        assert llm.requests == []
    finally:
        await engine.close()


class DriftLLM(ScriptedLLM):
    def __init__(self, responses, *, at="none", field="temperature", update=None):
        super().__init__(responses)
        self.at, self.field, self.update = at, field, update
        self.effective = []

    async def complete(self, messages, **kwargs):
        stage = ("planner" if "centralized planner" in messages[0]["content"] else "worker")
        self.effective.append((stage, kwargs.get("temperature"), kwargs.get("max_tokens")))
        response = await super().complete(messages, **kwargs)
        if self.at == stage:
            new = ("PRIVATE_DRIFT_VALUE" if self.field == "base_url" else
                   4096 if self.field == "max_tokens" else 0.7)
            setattr(self, self.field, new)
            if self.update is not None:
                self.update()
            if self.at == "worker" and self.field == "max_tokens":
                raise ConnectionError("synthetic transport failure")
        return response


@pytest.mark.asyncio
@pytest.mark.parametrize("at,field", [
    ("planner", "temperature"), ("planner", "max_tokens"), ("planner", "base_url"),
    ("before_worker", "temperature"), ("worker", "temperature"), ("worker", "max_tokens"),
    ("before_w2", "temperature"), ("before_synthesis", "temperature"),
    ("before_presentation", "temperature"), ("none", "temperature"),
])
async def test_native_model_boundaries_fail_closed_on_drift(tmp_path, monkeypatch, at, field):
    multi = at == "before_synthesis"
    request = frozen() if multi else frozen("Explain Alpha.")
    responses = ([plan_dict(), answer(1), answer(2), combined()] if multi
                 else [plan_dict(False), answer(1)])
    llm = DriftLLM(responses, at=at, field=field)
    validation = ScriptedLLM([verdict(ANSWERS if multi else ANSWERS[:1])])
    presentation = ScriptedLLM([]) if at == "before_presentation" else None
    engine = NativeMedAgentEngine(
        RuntimeConfig(trace_dir=str(tmp_path), worker_infrastructure_retry_base_delay_seconds=0),
        llm=llm, validation_llm=validation, presentation_llm=presentation,
        retrieval_backend=OffRetrievalBackend(),
    )
    if at == "before_worker":
        original = AgentLoop.execute
        async def change_before_worker(loop, *args, **kwargs):
            llm.temperature = 0.7
            return await original(loop, *args, **kwargs)
        monkeypatch.setattr(AgentLoop, "execute", change_before_worker)
    elif at == "before_w2":
        original = SemanticValidator.accept
        async def change_before_w2(validator, **kwargs):
            llm.temperature = 0.7
            return await original(validator, **kwargs)
        monkeypatch.setattr(SemanticValidator, "accept", change_before_w2)
    elif at == "before_synthesis":
        original = engine._synthesize
        async def change_before_synthesis(*args, **kwargs):
            llm.temperature = 0.7
            return await original(*args, **kwargs)
        engine._synthesize = change_before_synthesis
    elif at == "before_presentation":
        from medagent.runtime.native_engine import transform_presentation
        async def change_before_presentation(*args, **kwargs):
            presentation.temperature = 0.7
            return await transform_presentation(*args, **kwargs)
        monkeypatch.setattr("medagent.runtime.native_engine.transform_presentation",
                            change_before_presentation)
    try:
        if at == "none":
            run = await engine.analyze_frozen(request)
            assert run.result["status"] == "completed"
            assert llm.effective == [("planner", 0.0, 8192), ("worker", 0.0, 8192)]
        else:
            with pytest.raises(EvaluationConditionDriftError, match="model_parameters_drift"):
                await engine.analyze_frozen(request)
            expected_calls = (1 if at in {"planner", "before_worker"} else 3 if multi else 2)
            assert len(llm.requests) == expected_calls
            if at != "before_presentation":
                assert validation.requests == []
            else:
                assert presentation.requests == [] and len(validation.requests) == 1
            assert all(temp == 0.0 and limit == 8192 for _, temp, limit in llm.effective)
        events = read_trace(next(tmp_path.iterdir()))
        if at != "none":
            assert events[-1]["payload"]["status"] == "failed"
            error = [e["payload"] for e in events if e["event_type"] == "error"][-1]
            assert error["failure_stage"] == "evaluation"
            assert error["failure_reason"] == "model_parameters_drift"
            assert "PRIVATE_DRIFT_VALUE" not in json.dumps(events)
            assert not any(e["event_type"] == "worker_infrastructure_retry_scheduled"
                           for e in events)
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_openai_compatible_mock_honors_pinned_overrides_and_aborts_planner_drift(tmp_path):
    client = OpenAICompatibleLLM(base_url="https://synthetic.invalid/v1", api_key="offline-only",
                                 model="mock-model", temperature=0.0, max_tokens=1200)
    await client._client.aclose()
    bodies = []
    async def transport(request):
        bodies.append(json.loads(request.content))
        client.temperature = 0.7
        return httpx.Response(200, json={"choices": [{"message": {
            "content": json.dumps(plan_dict(False))}, "finish_reason": "stop"}]})
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    engine = NativeMedAgentEngine(RuntimeConfig(trace_dir=str(tmp_path)), llm=client,
                                  validation_llm=ScriptedLLM([]),
                                  retrieval_backend=OffRetrievalBackend())
    try:
        with pytest.raises(EvaluationConditionDriftError):
            await engine.analyze_frozen(frozen("Explain Alpha."))
        assert len(bodies) == 1
        assert bodies[0]["temperature"] == 0.0 and bodies[0]["max_tokens"] == 8192
        events = read_trace(next(tmp_path.iterdir()))
        assert not any(e["event_type"] == "worker_model_round" for e in events)
    finally:
        await engine.close()
