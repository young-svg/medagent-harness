"""Trusted evaluation seams and pinned-W6 behavioral parity, entirely offline."""
from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from functools import lru_cache
from hashlib import sha256
from pathlib import Path

import pytest

from medagent.context.request_spec import RequestItem, RequestSpec, build_request_spec
from medagent.guardrails.contract_checker import CheckResult, Guardrail
from medagent.llm import LLMResponse, ScriptedLLM
from medagent.memory.session import SessionMemory
from medagent.observability.privacy import metadata_payload
from medagent.observability.replay import read_trace
from medagent.retrieval.backend import FakeRetrievalBackend, OffRetrievalBackend
from medagent.runtime.config import RuntimeConfig
from medagent.runtime.engine import StageGenerationError
from medagent.runtime.evaluation_inputs import (
    FrozenExecutionPlan,
    FrozenRequest,
    canonical_json,
    content_sha256,
)
from medagent.runtime.native_engine import NativeMedAgentEngine
from medagent.runtime.semantic_validation import SemanticFailure, contract_for_spec
from tests.test_semantic_validation import inventory, verdict
from tests.test_w5_evidence_closure import source as synthetic_source

BASELINE = "59a783f39e23bef514dffa7ab416373f11c2dca2"
BASELINE_SOURCE_SHA256 = "e7e2da3e6e7bcbd0f51e27bb8626f095420bf625d576b432c7f59794e3d0eced"
DESCRIPTION = "SYNTHETIC case; no patient-specific facts supplied."
QUESTION = "Explain Alpha. Explain Beta."
ANSWERS = ["Alpha is an illustrative concept.", "Beta is a separate illustrative concept."]


def frozen(question=QUESTION, optional=False):
    texts = ["Explain Alpha.", "Explain Beta."] if question == QUESTION else [question]
    spec = RequestSpec([
        RequestItem(f"RQ{i}", text, not (optional and i == 2), i, text, "UNKNOWN")
        for i, text in enumerate(texts, 1)
    ])
    return FrozenRequest.capture(DESCRIPTION, question, spec, contract_for_spec(spec, []))


def plan_dict(two=True):
    tasks = [{"subtask_id": "first", "description": "Explain Alpha.",
              "assigned_agent": "diagnostic_agent", "request_item_ids": ["RQ1"],
              "deliverable_ids": ["COMPREHENSIVE_CASE_ANALYSIS"], "depends_on": []}]
    if two:
        tasks.append({"subtask_id": "second", "description": "Explain Beta using upstream.",
                      "assigned_agent": "consultation_agent", "request_item_ids": ["RQ2"],
                      "deliverable_ids": ["COMPREHENSIVE_CASE_ANALYSIS"],
                      "depends_on": ["first"]})
    return {"subtasks": tasks}


def answer(index):
    return {"answers": [{"request_item_id": f"RQ{index}", "answer": ANSWERS[index - 1]}]}


def combined():
    return {"answers": [{"request_item_id": f"RQ{i}", "answer": a}
                        for i, a in enumerate(ANSWERS, 1)]}


async def run_c(tmp_path, request=None, *, replay=None, config=None, responses=None,
                validation=None, engine_type=NativeMedAgentEngine, backend=None):
    request = request or frozen()
    if responses is None:
        responses = ([plan_dict()] if replay is None else []) + [answer(1), answer(2), combined()]
    llm = ScriptedLLM(responses)
    validator = ScriptedLLM(validation if validation is not None else [verdict(ANSWERS)])
    engine = engine_type(
        config or RuntimeConfig(trace_dir=str(tmp_path),
                                max_concurrent_workers=1 if replay else 4),
        llm=llm, validation_llm=validator,
        retrieval_backend=backend if backend is not None else OffRetrievalBackend(),
    )
    try:
        result = await engine.analyze_frozen(request, "same-session", plan=replay)
        return result, llm, validator, read_trace(tmp_path / result.result["run_id"])
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_frozen_request_bypasses_w1_but_retains_w2_and_immutable_inputs(tmp_path):
    spec = RequestSpec([RequestItem("RQ1", "Explain Alpha.", True, 1, "Explain Alpha.", "UNKNOWN")])
    contract = contract_for_spec(spec, [])
    request = FrozenRequest.capture(DESCRIPTION, "Explain Alpha.", spec, contract)
    spec.items.clear()
    contract.avoid.clear()
    run, llm, validator, events = await run_c(
        tmp_path, request, responses=[plan_dict(False), answer(1)],
        validation=[verdict(ANSWERS[:1])],
    )
    assert run.result["status"] == "completed"
    assert len(llm.requests) == 2 and len(validator.requests) == 1
    assert run.result["semantic_validation"]["calls"] == 1
    assert not any(e["stage"] in {"request_extraction", "request_verification"} for e in events)
    worker = json.loads(llm.requests[1]["messages"][1]["content"])
    assert worker["request_spec"]["items"][0]["id"] == "RQ1"
    assert "unsupported patient facts" in worker["contract"]["avoid"]
    assert next(e for e in events if e["event_type"] == "request_spec_validation")[
        "payload"]["request_source"] == "frozen"


@pytest.mark.asyncio
async def test_c_serial_replays_identical_post_policy_plan_without_planner(tmp_path, monkeypatch):
    c, c_llm, _, events = await run_c(tmp_path / "c")
    snapshot = FrozenExecutionPlan.from_dict(json.loads(json.dumps(c.plan.to_dict())))
    def forbidden_policy(*args, **kwargs):
        raise AssertionError("Replay must not regenerate or rewrite the plan")
    monkeypatch.setattr("medagent.runtime.native_engine.apply_contract_policy", forbidden_policy)
    serial, serial_llm, _, serial_events = await run_c(tmp_path / "serial", replay=snapshot)
    assert c.result["status"] == serial.result["status"] == "completed"
    assert c.result["final_request_item_answers"] == serial.result["final_request_item_answers"]
    assert c.plan.sha256 == serial.plan.sha256 == snapshot.sha256
    assert c.plan.plan_json == serial.plan.plan_json
    assert len(c_llm.requests) == 4 and len(serial_llm.requests) == 3
    for recorded, source, count in [(events, "planner_policy", 1),
                                     (serial_events, "frozen_replay", 0)]:
        payload = next(e["payload"] for e in recorded if e["event_type"] == "plan_created")
        assert payload["plan_source"] == source
        assert payload["planner_call_count"] == count
        assert payload["plan_sha256"] == snapshot.sha256
        assert sum(e["event_type"] == "llm_request" and e["stage"] == "planning"
                   for e in recorded) == count
        by_id = {e["event_id"]: e for e in recorded}
        ready = next(e for e in recorded if e["event_type"] == "dependency_ready")
        assert ready["payload"]["handoff_event_ids"]
        handoff = by_id[ready["payload"]["handoff_event_ids"][0]]
        assert by_id[handoff["parent_event_id"]]["event_type"] == "worker_draft"
        assert recorded.index(handoff) < next(i for i, e in enumerate(recorded)
            if e["event_type"] == "llm_request" and e["agent"] == "consultation_agent")
        assert "Explain Alpha" not in json.dumps(recorded)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["digest", "rq", "missing", "deliverable", "cycle",
                                    "duplicate", "oversized", "budget", "tools", "role",
                                    "model_settings"])
async def test_invalid_replay_is_rejected_before_any_model_call(tmp_path, change):
    c, _, _, _ = await run_c(tmp_path / "capture")
    snapshot = c.plan
    value = json.loads(snapshot.plan_json)
    config = RuntimeConfig(trace_dir=str(tmp_path / "replay"), max_concurrent_workers=1)
    engine_type = NativeMedAgentEngine
    if change == "digest":
        snapshot = replace(snapshot, sha256="0" * 64)
    elif change == "budget":
        config = replace(config, worker_max_model_rounds=1)
    elif change == "tools":
        class RestrictedEngine(NativeMedAgentEngine):
            def _tool_registry(self, question, bundle, capabilities, **kwargs):
                return super()._tool_registry(question, bundle,
                                              {key: set() for key in capabilities}, **kwargs)
        engine_type = RestrictedEngine
    else:
        if change == "rq":
            value["subtasks"][0]["request_item_ids"] = ["RQ99"]
        elif change == "missing":
            value["subtasks"][1]["request_item_ids"] = ["RQ1"]
        elif change == "deliverable":
            value["subtasks"][0]["deliverable_ids"] = ["TREATMENT_PLAN"]
        elif change == "cycle":
            value["subtasks"][0]["depends_on"] = ["second"]
        elif change == "duplicate":
            value["subtasks"][1]["subtask_id"] = "first"
        elif change == "role":
            value["subtasks"][0]["assigned_agent"] = "administrator"
        elif change == "oversized":
            root = value["subtasks"][0]
            value["subtasks"] += [{**root, "subtask_id": f"extra-{i}"} for i in range(15)]
        snapshot = replace(snapshot, plan_json=canonical_json(value), sha256=content_sha256(value))
    llm, validation = ScriptedLLM([]), ScriptedLLM([])
    if change == "model_settings":
        llm.temperature = 0.5
    engine = engine_type(config, llm=llm, validation_llm=validation,
                         retrieval_backend=OffRetrievalBackend())
    try:
        with pytest.raises(ValueError if change != "oversized" else StageGenerationError):
            await engine.analyze_frozen(frozen(), plan=snapshot)
        assert llm.requests == validation.requests == []
        failed = read_trace(next((tmp_path / "replay").iterdir()))
        assert failed[-1]["payload"]["status"] == "failed"
        assert not any(e["event_type"] == "worker_model_round" for e in failed)
    finally:
        await engine.close()


@pytest.mark.parametrize("change", ["source", "id", "required", "priority", "scope", "avoid",
                                    "label"])
def test_frozen_request_rejects_inconsistent_or_unmapped_specs(change):
    request = frozen()
    spec = json.loads(request.spec_json)
    contract = json.loads(request.contract_json)
    if change == "source":
        spec["items"][0]["source_span"] = "invented request"
    elif change == "id":
        spec["items"][0]["id"] = "RQ99"
    elif change == "required":
        spec["items"][0]["required"] = "false"
    elif change == "priority":
        contract["optional_cover"] = ["comprehensive case analysis"]
    elif change == "scope":
        contract["requested_deliverables"] = ["TREATMENT_PLAN"]
    elif change == "avoid":
        contract["avoid"] = []
    elif change == "label":
        contract["must_cover"].append("invented deliverable")
    invalid = replace(request, spec_json=canonical_json(spec),
                      contract_json=canonical_json(contract))
    with pytest.raises(ValueError):
        invalid.materialize(DESCRIPTION, QUESTION)
    with pytest.raises(ValueError, match="current input"):
        request.materialize("Different case", QUESTION)


@pytest.mark.asyncio
@pytest.mark.parametrize("options", [{"semantic_validation_enabled": False},
                                     {"semantic_max_calls": 0}])
async def test_frozen_execution_cannot_disable_or_exhaust_w2_and_continue(tmp_path, options):
    llm = ScriptedLLM([])
    engine = NativeMedAgentEngine(RuntimeConfig(trace_dir=str(tmp_path), **options), llm=llm,
                                  retrieval_backend=OffRetrievalBackend())
    try:
        with pytest.raises((ValueError, SemanticFailure)):
            await engine.analyze_frozen(frozen())
        assert llm.requests == []
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_failed_upstream_replay_blocks_downstream_and_w2(tmp_path):
    c, _, _, _ = await run_c(tmp_path / "c")
    failed, llm, verifier, events = await run_c(
        tmp_path / "serial", replay=c.plan, responses=[LLMResponse("", finish_reason="stop")],
        validation=[],
    )
    assert failed.result["status"] == "incomplete"
    assert len(llm.requests) == 1 and verifier.requests == []
    assert any(e["event_type"] == "dependency_blocked" for e in events)
    assert not any(e["event_type"] == "llm_request" and e["agent"] == "consultation_agent"
                   for e in events)


@pytest.mark.parametrize("field", ["plan_sha256", "request_sha256",
                                    "execution_conditions_sha256"])
def test_evaluation_metadata_hashes_cannot_carry_free_text(field):
    assert metadata_payload({field: "a" * 64}) == {field: "a" * 64}
    for bad in ["a" * 64 + "\n", "PRIVATE_CASE", {"hash": "a" * 64}, "A" * 64]:
        assert metadata_payload({field: bad}) == {}


@lru_cache
def baseline_engine():
    # Exact pinned W6 blob: portable to a clone without unpublished Git ancestors.
    blob = (Path(__file__).parent / "fixtures" / "w6_native_engine.txt").read_bytes()
    assert sha256(blob).hexdigest() == BASELINE_SOURCE_SHA256
    source = blob.decode("utf-8")
    namespace = {"__name__": "w7_pinned_w6_native_engine"}
    exec(compile(source, "<pinned W6 NativeEngine>", "exec"), namespace)
    return namespace["NativeMedAgentEngine"]


def normalize(result, events):
    ids = {e["event_id"]: f"event-{i}" for i, e in enumerate(events)}
    ids[result["run_id"]] = "RUN"
    def clean(value):
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items() if k not in {"timestamp", "latency_ms"}}
        if isinstance(value, list):
            return [clean(v) for v in value]
        if isinstance(value, str):
            return ids.get(value, value)
        return value
    return clean({"result": result, "events": events})


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["completed", "incomplete", "hard_block", "evidence",
                                      "w2_failure", "optional"])
async def test_default_analyze_matches_pinned_w6_results_and_full_event_sequence(
    tmp_path, monkeypatch, scenario,
):
    question = "Explain Alpha."
    outcomes = [(question, "required")]
    responses = [plan_dict(False), answer(1)]
    checks = [verdict(ANSWERS[:1])]
    expected = "completed"
    if scenario == "incomplete":
        question = QUESTION
        outcomes = [("Explain Alpha.", "required"), ("Explain Beta.", "required")]
        responses[0]["subtasks"][0]["request_item_ids"] = ["RQ1", "RQ2"]
        checks, expected = [], "incomplete"
    elif scenario == "hard_block":
        monkeypatch.setattr(Guardrail, "check", lambda *a: CheckResult(
            False, blocking_issues=["synthetic_hard_block"]))
        checks, expected = [], "incomplete"
    elif scenario == "evidence":
        question = "Consult external materials and identify the sources."
        outcomes = [(question, "required")]
        checks, expected = [], "incomplete"
    elif scenario == "w2_failure":
        checks, expected = [verdict(ANSWERS[:1], ["off_topic"])], "incomplete"
    elif scenario == "optional":
        question = "Explain Alpha. Optional: discuss Beta."
        outcomes = [("Explain Alpha.", "required"), ("Optional: discuss Beta.", "optional")]
        responses[0]["subtasks"][0]["request_item_ids"] = ["RQ1", "RQ2"]
        checks = [verdict([ANSWERS[0], ""], ["satisfied", "unanswered"])]
    normalized = []
    for label, engine_type in [("old", baseline_engine()), ("new", NativeMedAgentEngine)]:
        import copy
        llm = ScriptedLLM(copy.deepcopy(responses))
        validator = ScriptedLLM([inventory(question, outcomes), inventory(question, outcomes),
                                 *copy.deepcopy(checks)])
        engine = engine_type(RuntimeConfig(trace_dir=str(tmp_path / label)), llm=llm,
                             validation_llm=validator, retrieval_backend=OffRetrievalBackend())
        try:
            result = await engine.analyze(DESCRIPTION, question, "parity")
            assert result["status"] == expected
            events = read_trace(tmp_path / label / result["run_id"])
            normalized.append(normalize(result, events))
        finally:
            await engine.close()
    assert normalized[0] == normalized[1]


@pytest.mark.asyncio
async def test_frozen_plan_replay_changes_only_actual_worker_concurrency(tmp_path):
    question = ("Provide a comprehensive answer:\n1. Give a diagnosis.\n"
                "2. Give a treatment plan.\n3. List further tests.\n4. Discuss prevention.")
    spec = build_request_spec(question)
    contract = contract_for_spec(spec, [])
    request = FrozenRequest.capture(DESCRIPTION, question, spec, contract)
    planned = {"subtasks": [
        {"subtask_id": "d", "description": "Give diagnosis and further tests.",
         "assigned_agent": "diagnostic_agent", "request_item_ids": ["RQ1", "RQ3"],
         "deliverable_ids": ["DIAGNOSIS_WITH_BASIS", "FURTHER_TESTS"]},
        {"subtask_id": "c", "description": "Give treatment plan and prevention.",
         "assigned_agent": "consultation_agent", "request_item_ids": ["RQ2", "RQ4"],
         "deliverable_ids": ["TREATMENT_PLAN", "PREVENTION"]},
    ]}
    texts = ["Diagnosis with basis: provisional synthetic hypothesis.",
             "Treatment plan: conditional discussion only.",
             "Further tests: consider collecting additional information.",
             "Prevention: general synthetic considerations."]
    def subset(indices):
        return {"answers": [{"request_item_id": f"RQ{i}", "answer": texts[i - 1]}
                            for i in indices]}
    class ObservedLLM(ScriptedLLM):
        active = 0
        peak = 0
        async def complete(self, messages, **kwargs):
            worker = "Public procedural skill (" in messages[0]["content"] and (
                "Public procedural skill (synthesis)" not in messages[0]["content"])
            if worker:
                self.active += 1
                self.peak = max(self.peak, self.active)
            try:
                if worker:
                    await asyncio.sleep(0.01)
                return await super().complete(messages, **kwargs)
            finally:
                if worker:
                    self.active -= 1
    snapshots = []
    for name, concurrency in [("c", 4), ("serial", 1)]:
        responses = ([planned] if name == "c" else []) + [subset([1, 3]), subset([2, 4]),
                                                         subset([1, 2, 3, 4])]
        llm = ObservedLLM(responses)
        engine = NativeMedAgentEngine(
            RuntimeConfig(trace_dir=str(tmp_path / name), max_concurrent_workers=concurrency),
            llm=llm, validation_llm=ScriptedLLM([verdict(texts)]),
            retrieval_backend=OffRetrievalBackend(),
        )
        try:
            run = await engine.analyze_frozen(request, plan=snapshots[0] if snapshots else None)
            assert run.result["status"] == "completed"
            snapshots.append(run.plan)
            assert llm.peak == (2 if name == "c" else 1)
        finally:
            await engine.close()
    assert snapshots[0].sha256 == snapshots[1].sha256
    assert snapshots[0].conditions_sha256 == snapshots[1].conditions_sha256


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["history", "fake_fixture"])
async def test_changed_history_or_fake_corpus_rejects_replay_without_leaking_text(tmp_path, change):
    initial_backend = (FakeRetrievalBackend([synthetic_source()])
                       if change == "fake_fixture" else None)
    captured, _, _, _ = await run_c(tmp_path / "c", backend=initial_backend)
    memory = SessionMemory()
    if change == "history":
        memory.add("same-session", "user", "PRIVATE_CHANGED_HISTORY")
        backend = OffRetrievalBackend()
    else:
        backend = FakeRetrievalBackend([synthetic_source(text="PRIVATE_CHANGED_EVIDENCE")])
    llm = ScriptedLLM([])
    engine = NativeMedAgentEngine(
        RuntimeConfig(trace_dir=str(tmp_path / "serial"), max_concurrent_workers=1),
        llm=llm, validation_llm=ScriptedLLM([]), memory=memory, retrieval_backend=backend,
    )
    try:
        with pytest.raises(ValueError, match="conditions mismatch"):
            await engine.analyze_frozen(frozen(), "same-session", plan=captured.plan)
        assert llm.requests == []
        events = read_trace(next((tmp_path / "serial").iterdir()))
        assert events[-1]["payload"]["status"] == "failed"
        assert "PRIVATE_CHANGED" not in json.dumps(events)
    finally:
        await engine.close()
