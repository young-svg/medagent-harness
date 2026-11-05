"""W1/W2 counterexamples: all clients are scripted; no provider/corpus access."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from medagent.guardrails.contract_checker import CheckResult, Guardrail
from medagent.guardrails.stable_patch import EditAction, StableEdit
from medagent.llm.client import LLMResponse
from medagent.llm.fakes import ScriptedLLM
from medagent.observability.replay import read_trace, replay
from medagent.observability.tracer import TraceRecorder
from medagent.runtime.config import RuntimeConfig
from medagent.runtime.native_engine import NativeMedAgentEngine
from medagent.runtime.semantic_validation import (
    SemanticFailure,
    SemanticValidator,
    contract_for_spec,
    parse_inventory,
)
from tests.test_release_integration import dependency_plan


def inventory(question, outcomes):
    cursor, segments = 0, []
    for text, kind in outcomes:
        start = question.index(text, cursor)
        if start > cursor:
            segments.append({"start": cursor, "end": start, "kind": "context"})
        end = start + len(text)
        segments.append({"start": start, "end": end, "kind": kind})
        cursor = end
    if cursor < len(question):
        segments.append({"start": cursor, "end": len(question), "kind": "context"})
    return {"segments": segments}


def verdict(answers, statuses=None, restrictions=(), issues=()):
    statuses = statuses or ["satisfied"] * len(answers)
    return {
        "items": [{"request_item_id": f"RQ{i}", "status": status, "evidence": answer,
                   "reason": "The quoted content supports this item verdict."}
                  for i, (answer, status) in enumerate(zip(answers, statuses, strict=True), 1)],
        "restrictions": [{"index": i, "status": status, "evidence": "",
                          "reason": "No prohibited action appears."}
                         for i, status in enumerate(restrictions)],
        "guardrail": [{"index": i, "status": status, "evidence": answers[0],
                       "reason": "Content reviewed against case and requested scope."}
                      for i, status in enumerate(issues)],
    }


def validator(tmp_path, responses, **config):
    llm = ScriptedLLM(responses)
    gate = SemanticValidator(llm, RuntimeConfig(**config), TraceRecorder(
        str(tmp_path), content_mode="raw"))
    return gate, llm


@pytest.mark.asyncio
@pytest.mark.parametrize("question,outcomes", [
    ("Explain causes and summarize warning signs.",
     [("Explain causes", "required"), ("summarize warning signs.", "required")]),
    ("请解释病因，并说明预防方法。", [("请解释病因", "required"), ("说明预防方法。", "required")]),
    ("1. Explain causes.\n2. Summarize warning signs.",
     [("Explain causes.", "required"), ("Summarize warning signs.", "required")]),
    ("What are the causes?\nWhat are the warning signs?",
     [("What are the causes?", "required"), ("What are the warning signs?", "required")]),
    ("Explain causes.", [("Explain causes.", "required")]),
    ("optional: summarize warning signs.", [("optional: summarize warning signs.", "optional")]),
    ("1. Explain causes and summarize warning signs.\n2. Discuss prevention.", [
        ("Explain causes", "required"), ("summarize warning signs.", "required"),
        ("Discuss prevention.", "required")]),
    ("解释病因。可选：说明治疗。不要作出诊断。", [
        ("解释病因。", "required"), ("可选：说明治疗。", "optional"),
        ("不要作出诊断。", "prohibited")]),
    ("Explain causes; optional: discuss treatment. Do not diagnose.", [
        ("Explain causes", "required"), ("optional: discuss treatment.", "optional"),
        ("Do not diagnose.", "prohibited")]),
])
async def test_source_inventory_preserves_natural_language_and_scope(tmp_path, question, outcomes):
    extraction = inventory(question, outcomes)
    gate, llm = validator(tmp_path, [extraction, extraction])
    spec, restrictions = await gate.extract(question)
    assert len(spec.items) == sum(kind in {"required", "optional"} for _, kind in outcomes)
    assert restrictions == [text for text, kind in outcomes if kind == "prohibited"]
    assert all(item.source_span in question and item.text == item.source_span
               for item in spec.items)
    assert spec.required_item_ids == [f"RQ{i}" for i, item in enumerate(spec.items, 1)
                                     if item.required]
    assert len(llm.requests) == 2
    # The second extraction is blind to the candidate and its claimed judgments.
    assert json.loads(llm.requests[1]["messages"][1]["content"]) == {
        "question": question, "case": "", "bounded_session_context": [],
    }
    contract = contract_for_spec(spec, restrictions)
    if restrictions:
        assert "DIAGNOSIS_WITH_BASIS" not in contract.requested_deliverables
        assert "treatment plan" not in contract.must_cover
        assert "treatment plan" in contract.optional_cover
        assert all(text in contract.avoid for text in restrictions)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missed", "over_split", "optional", "negation"])
async def test_blind_inventory_disagreement_never_dispatches(tmp_path, fault):
    question = "Explain causes and summarize warning signs."
    correct = inventory(question, [("Explain causes", "required"),
                                   ("summarize warning signs.", "required")])
    faulty = inventory(question, [(question, "required")])
    if fault == "over_split":
        faulty = inventory(question, [("Explain", "required"), ("causes", "required"),
                                      ("summarize warning signs.", "required")])
    if fault in {"optional", "negation"}:
        faulty = json.loads(json.dumps(correct))
        faulty["segments"][0]["kind"] = "optional" if fault == "optional" else "prohibited"
    worker, checker = ScriptedLLM([]), ScriptedLLM([faulty, correct])
    engine = NativeMedAgentEngine(RuntimeConfig(trace_content_mode="raw", trace_dir=str(tmp_path)),
                                 llm=worker, validation_llm=checker)
    result = await engine.analyze("Synthetic case", question)
    await engine.close()
    assert result["status"] == "incomplete"
    assert result["semantic_validation"]["status"] == "uncertain"
    assert not worker.requests
    assert not result["trace"]["user_request_complete"]


@pytest.mark.parametrize("bad", [
    {"segments": [{"start": 0, "end": 999, "kind": "required"}]},
    {"segments": [{"start": 1, "end": 5, "kind": "required"}]},
    {"segments": [{"start": False, "end": 5, "kind": "required"}]},
    {"segments": [{"start": 0, "end": 5, "kind": "invented"}]},
    {"segments": [{"start": 0, "end": 5, "kind": "required", "text": "Invented"}]},
    {"segments": []},
    {"segments": "not a list"},
    {"segments": [{"start": 0, "end": 3, "kind": "required"},
                  {"start": 2, "end": 5, "kind": "required"}]},
])
def test_program_rejects_invented_or_invalid_source_schema(bad):
    with pytest.raises(ValueError):
        parse_inventory(bad, "hello")


@pytest.mark.parametrize("question", ["Do not diagnose.", "不要诊断。", "optional: treatment",
                                      "1. Explain.\n2. Summarize."])
def test_program_scope_and_duplicate_constraints(question):
    with pytest.raises(ValueError):
        parse_inventory(inventory(question, [(question, "required")]), question)


def test_duplicate_source_outcomes_are_rejected():
    question = "Explain. Explain."
    with pytest.raises(ValueError, match="duplicate"):
        parse_inventory(inventory(question, [("Explain.", "required"),
                                             ("Explain.", "required")]), question)


@pytest.mark.asyncio
@pytest.mark.parametrize("response", ["not JSON", '{"segments":[],"segments":[]}',
                                     LLMResponse("{}", finish_reason="length"),
                                     {"segments": [{"start": 0, "end": 500,
                                                    "kind": "required"}]}])
async def test_invalid_extraction_has_no_retry_or_worker(tmp_path, response):
    gate, llm = validator(tmp_path, [response])
    with pytest.raises(SemanticFailure):
        await gate.extract("Explain causes")
    assert len(llm.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("budget,expected", [(0, 0), (1, 1)])
async def test_extraction_budget_exhaustion(tmp_path, budget, expected):
    question = "Explain causes"
    extraction = inventory(question, [(question, "required")])
    gate, llm = validator(tmp_path, [extraction, extraction], semantic_max_calls=budget)
    with pytest.raises(SemanticFailure, match="semantic_budget_exhausted"):
        await gate.extract(question)
    assert len(llm.requests) == expected


@pytest.mark.asyncio
async def test_only_prohibitions_do_not_become_work(tmp_path):
    question = "Do not diagnose."
    extraction = inventory(question, [(question, "prohibited")])
    gate, _ = validator(tmp_path, [extraction, extraction])
    with pytest.raises(SemanticFailure, match="no_actionable_request"):
        await gate.extract(question)


QUESTION = "Explain causes and summarize warning signs."
OUTCOMES = [("Explain causes", "required"), ("summarize warning signs.", "required")]
ANSWERS = ["Causes include synthetic factor Alpha.", "Warning signs include synthetic sign Beta."]


async def run_single(tmp_path, *, statuses=None, final_verdict=None, budget=3,
                     worker_answers=None, question=QUESTION, outcomes=OUTCOMES):
    extraction = inventory(question, outcomes)
    texts = worker_answers if worker_answers is not None else ANSWERS
    worker = ScriptedLLM([
        {"subtasks": [{"subtask_id": "s1", "description": "Address the source requests",
                       "assigned_agent": "diagnostic_agent", "request_item_ids": ["RQ1", "RQ2"]}]},
        {"answers": [{"request_item_id": f"RQ{i}", "answer": text}
                     for i, text in enumerate(texts, 1)]},
    ])
    check = ScriptedLLM([extraction, extraction,
                         final_verdict if final_verdict is not None else verdict(texts, statuses)])
    engine = NativeMedAgentEngine(RuntimeConfig(
        trace_content_mode="raw",
        trace_dir=str(tmp_path), semantic_max_calls=budget),
                                 llm=worker, validation_llm=check)
    try:
        result = await engine.analyze("Synthetic case only", question)
    finally:
        await engine.close()
    return result, worker, check


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["satisfied", "unanswered", "off_topic",
                                    "insufficient_information", "uncertain", "execution_error"])
async def test_final_item_acceptance_distinguishes_failure_reasons(tmp_path, status):
    result, worker, check = await run_single(tmp_path, statuses=["satisfied", status])
    accepted = status == "satisfied"
    assert result["status"] == ("completed" if accepted else "incomplete")
    assert result["structural_user_request_complete"] is True
    assert result["user_request_complete"] is accepted
    assert result["contract_complete"] is accepted
    assert result["semantic_validation"]["items"][1]["status"] == status
    assert result["trace"]["user_request_complete"] is accepted
    assert len(check.requests) == 3 and len(worker.requests) == 2
    payload = json.loads(check.requests[-1]["messages"][1]["content"])
    assert set(payload) == {"question", "case", "request_spec", "contract", "restrictions",
                            "final_answers", "guardrail_issues", "bounded_session_context"}
    assert "previous" not in payload and "workers" not in payload
    assert replay(tmp_path / result["run_id"])["summary"]["user_request_complete"] is accepted


@pytest.mark.asyncio
async def test_partial_worker_answers_stop_before_synthesis_and_acceptance(tmp_path):
    result, worker, check = await run_single(tmp_path, worker_answers=ANSWERS[:1])
    assert result["status"] == "incomplete"
    assert result["missing_required_request_items"] == ["RQ1", "RQ2"]
    assert result["trace"]["worker_user_request_complete"] is False
    assert len(check.requests) == 2 and len(worker.requests) == 2
    assert "synthesis_input" not in result["trace"]["events"]


@pytest.mark.asyncio
async def test_nonempty_irrelevant_answer_cannot_complete(tmp_path):
    result, _, _ = await run_single(tmp_path, statuses=["satisfied", "off_topic"],
                                    worker_answers=[ANSWERS[0], "Tomorrow will be sunny."])
    assert result["structural_user_request_complete"] is True
    assert result["status"] == "incomplete"


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["duplicate", "unknown", "missing", "evidence", "empty_evidence",
                                   "bad_json", "extra_field", "invalid_status", "bad_reason"])
async def test_invalid_semantic_verdict_fails_closed(tmp_path, fault):
    response = verdict(ANSWERS)
    if fault == "duplicate":
        response["items"][1] = response["items"][0]
    elif fault == "unknown":
        response["items"][1]["request_item_id"] = "RQ99"
    elif fault == "missing":
        response["items"].pop()
    elif fault == "evidence":
        response["items"][1]["evidence"] = "Fabricated supporting evidence"
    elif fault == "empty_evidence":
        response["items"][1]["evidence"] = ""
    elif fault == "bad_json":
        response = "not json"
    elif fault == "extra_field":
        response["passed"] = True
    elif fault == "bad_reason":
        response["items"][0]["reason"] = ""
    else:
        response["items"][1]["status"] = "approved"
    result, _, check = await run_single(tmp_path, final_verdict=response)
    assert result["status"] == "incomplete"
    assert result["semantic_validation"]["status"] == "uncertain"
    assert len(check.requests) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("budget", [0, 1, 2])
async def test_no_business_or_validation_calls_when_full_budget_cannot_fit(tmp_path, budget):
    result, worker, check = await run_single(tmp_path, budget=budget)
    assert result["status"] == "incomplete"
    assert result["semantic_validation"]["reason"] == "semantic_budget_exhausted"
    assert not check.requests and not worker.requests


@pytest.mark.asyncio
async def test_unanswered_optional_output_does_not_block_required_answer(tmp_path):
    question = "Explain causes; optional: discuss treatment."
    outcomes = [("Explain causes", "required"), ("optional: discuss treatment.", "optional")]
    response = verdict([ANSWERS[0], ""], ["satisfied", "unanswered"],
                       issues=["false_positive"])
    result, _, _ = await run_single(tmp_path, question=question, outcomes=outcomes,
                                    worker_answers=ANSWERS[:1], final_verdict=response)
    assert result["status"] == "completed"
    assert result["final_request_item_answers"] == {"RQ1": ANSWERS[0]}


@pytest.mark.asyncio
async def test_missing_final_answer_cannot_be_approved_by_verifier(tmp_path):
    gate, _ = validator(tmp_path, [verdict([""], ["satisfied"])])
    from medagent.context.request_spec import build_request_spec

    spec = build_request_spec("Explain causes")
    result = await gate.accept(question="Explain causes", description="Synthetic", spec=spec,
                               contract=contract_for_spec(spec, []), restrictions=[],
                               answers={}, guardrail=CheckResult(True))
    assert result["complete"] is False
    assert result["items"][0]["status"] == "unanswered"


@pytest.mark.asyncio
@pytest.mark.parametrize("hard,issue_verdict,expected", [
    (False, "false_positive", "completed"), (False, "confirmed", "incomplete"),
    (False, "uncertain", "incomplete"), (True, "false_positive", "incomplete"),
])
async def test_guardrail_false_positive_and_explicit_block(tmp_path, monkeypatch, hard,
                                                         issue_verdict, expected):
    def check(*args):
        return CheckResult(False, ["missing_deliverable:COMPREHENSIVE_CASE_ANALYSIS"],
                           blocking_issues=["explicit_policy_block"] if hard else [])
    monkeypatch.setattr(Guardrail, "check", check)
    response = verdict(ANSWERS, issues=[issue_verdict])
    result, _, _ = await run_single(tmp_path, final_verdict=response)
    assert result["status"] == expected


@pytest.mark.asyncio
async def test_rejected_safety_patch_is_a_program_block(tmp_path, monkeypatch):
    def check(*args):
        return CheckResult(False, ["contradiction:missing-unit:synthetic"],
                           [StableEdit("missing-unit", EditAction.DELETE)])
    monkeypatch.setattr(Guardrail, "check", check)
    result, _, _ = await run_single(tmp_path, final_verdict=verdict(
        ANSWERS, issues=["false_positive"],
    ))
    assert result["status"] == "incomplete"
    assert result["semantic_validation"]["blocking_issues"] == ["safety_patch_rejected"]


@pytest.mark.asyncio
async def test_acceptance_uses_actual_post_edit_answer(tmp_path, monkeypatch):
    unrelated = "Tomorrow will be sunny."

    def check(self, answer, *args):
        if ANSWERS[1] in answer:
            return CheckResult(False, ["contradiction:U2:synthetic"],
                               [StableEdit("U2", EditAction.REPLACE, unrelated)])
        return CheckResult(True)
    monkeypatch.setattr(Guardrail, "check", check)
    result, _, checker = await run_single(tmp_path, final_verdict=verdict(
        [ANSWERS[0], unrelated], ["satisfied", "off_topic"],
    ))
    assert result["status"] == "incomplete"
    assert result["structural_user_request_complete"] is True
    final_payload = json.loads(checker.requests[-1]["messages"][1]["content"])
    assert final_payload["final_answers"]["RQ2"] == unrelated
    assert ANSWERS[1] not in checker.requests[-1]["messages"][1]["content"]


@pytest.mark.asyncio
async def test_default_shared_client_uses_fresh_conversations_and_fixed_budget(tmp_path):
    question, answer = "Explain causes.", ANSWERS[0]
    extraction = inventory(question, [(question, "required")])
    llm = ScriptedLLM([
        extraction, extraction,
        {"subtasks": [{"description": "Explain causes", "assigned_agent": "diagnostic_agent"}]},
        answer, verdict([answer]),
    ])
    engine = NativeMedAgentEngine(RuntimeConfig(
        trace_content_mode="raw",
        trace_dir=str(tmp_path)), llm=llm)
    result = await engine.analyze("Synthetic case", question)
    await engine.close()
    assert result["status"] == "completed"
    assert result["semantic_validation"]["calls"] == 3
    assert result["trace"]["llm_calls"] == len(llm.requests) == 5
    assert len(llm.requests[-1]["messages"]) == 2
    assert all(message["role"] != "assistant" for message in llm.requests[-1]["messages"])


@pytest.mark.asyncio
async def test_worker_execution_error_keeps_existing_error_protocol(tmp_path, monkeypatch):
    from medagent.runtime.agent_loop import AgentLoop

    async def fail(*args, **kwargs):
        raise RuntimeError("offline injected worker failure")
    monkeypatch.setattr(AgentLoop, "execute", fail)
    question = "Explain causes."
    extraction = inventory(question, [(question, "required")])
    llm = ScriptedLLM([extraction, extraction, {"subtasks": [{
        "description": "Explain causes", "assigned_agent": "diagnostic_agent",
    }]}])
    engine = NativeMedAgentEngine(RuntimeConfig(
        trace_content_mode="raw",
        trace_dir=str(tmp_path)), llm=llm)
    result = await engine.analyze("Synthetic", question)
    assert result["status"] == "incomplete"
    assert result["execution_failures"][0]["worker_status"] == "execution_error"
    await engine.close()
    assert len(llm.requests) == 3


@pytest.mark.asyncio
async def test_verifier_provider_error_is_execution_error_without_retry(tmp_path):
    class Failing(ScriptedLLM):
        async def complete(self, *args, **kwargs):
            raise RuntimeError("offline injected verifier failure")
    gate = SemanticValidator(Failing([]), RuntimeConfig(), TraceRecorder(
        str(tmp_path), content_mode="raw"))
    with pytest.raises(SemanticFailure) as error:
        await gate.extract("Explain")
    assert error.value.status == "execution_error"
    assert gate.calls == 1


@pytest.mark.asyncio
async def test_real_keyword_guardrail_false_positive_can_be_reviewed(tmp_path):
    question = "Give a diagnosis and describe a treatment plan."
    outcomes = [("Give a diagnosis", "required"), ("describe a treatment plan.", "required")]
    texts = ["Synthetic Alpha is the provisional assessment.", "Consider synthetic care Beta."]
    result, _, _ = await run_single(tmp_path, question=question, outcomes=outcomes,
                                    worker_answers=texts,
                                    final_verdict=verdict(texts, issues=["false_positive"] * 2))
    assert result["status"] == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("violate", [True, False])
async def test_prohibition_is_passed_to_workers_and_final_gate(tmp_path, violate):
    question = "Explain causes. Do not diagnose."
    outcomes = [("Explain causes.", "required"), ("Do not diagnose.", "prohibited")]
    text = "Causes include Alpha."
    result, worker, _ = await run_single(
        tmp_path, question=question, outcomes=outcomes, worker_answers=[text],
        final_verdict=verdict([text], restrictions=["violated" if violate else "satisfied"]),
    )
    assert result["status"] == ("incomplete" if violate else "completed")
    assert "Do not diagnose." in worker.requests[1]["messages"][1]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_synthesis", [False, True])
async def test_semantic_gate_preserves_dag_handoff_and_synthesis(tmp_path, bad_synthesis):
    question = "1. Give a diagnosis.\n2. Give a treatment plan based on that diagnosis."
    outcomes = [("Give a diagnosis.", "required"),
                ("Give a treatment plan based on that diagnosis.", "required")]
    texts = ["Diagnosis with basis: synthetic Alpha.", "Treatment plan: synthetic Beta."]
    extraction = inventory(question, outcomes)
    final = [texts[0], texts[0] if bad_synthesis else texts[1]]
    worker = ScriptedLLM([
        dependency_plan(),
        {"answers": [{"request_item_id": "RQ1", "answer": texts[0]}]},
        {"answers": [{"request_item_id": "RQ2", "answer": texts[1]}]},
        {"answers": [{"request_item_id": f"RQ{i}", "answer": text}
                     for i, text in enumerate(final, 1)]},
    ])
    check = ScriptedLLM([extraction, extraction, verdict(
        final, ["satisfied", "off_topic" if bad_synthesis else "satisfied"],
        issues=["confirmed"] if bad_synthesis else [],
    )])
    engine = NativeMedAgentEngine(RuntimeConfig(trace_content_mode="raw", trace_dir=str(tmp_path)),
                                 llm=worker, validation_llm=check)
    result = await engine.analyze("Synthetic case only", question)
    await engine.close()
    assert result["status"] == ("incomplete" if bad_synthesis else "completed")
    events = read_trace(tmp_path / result["run_id"])
    assert sum(e["event_type"] == "worker_handoff" for e in events) == 1
    assert "synthesis_input" in result["trace"]["events"]
    assert len(worker.requests) == 4 and len(check.requests) == 3


@pytest.mark.asyncio
async def test_input_and_timeout_failures_are_bounded(tmp_path):
    gate, llm = validator(tmp_path, [], semantic_max_input_chars=1)
    with pytest.raises(SemanticFailure, match="semantic_input_budget_exceeded"):
        await gate.extract("Explain")
    assert not llm.requests

    class Slow(ScriptedLLM):
        async def complete(self, *args, **kwargs):
            await asyncio.sleep(10)
    gate = SemanticValidator(Slow([]), RuntimeConfig(semantic_timeout_seconds=0.01),
                             TraceRecorder(str(tmp_path), content_mode="raw"))
    with pytest.raises(SemanticFailure) as error:
        await gate.extract("Explain")
    assert error.value.status == "execution_error"
    assert gate.calls == 1


@pytest.mark.parametrize("kwargs", [
    {"semantic_max_calls": 4}, {"semantic_max_calls": True},
    {"semantic_max_tokens": 0}, {"semantic_timeout_seconds": 0},
    {"semantic_timeout_seconds": 61}, {"semantic_max_input_chars": 0},
    {"semantic_validation_enabled": "yes"},
])
def test_semantic_configuration_hard_limits(kwargs):
    with pytest.raises(ValueError):
        RuntimeConfig(**kwargs)


def test_semantics_enabled_by_default_and_api_reports_uncertainty(tmp_path, monkeypatch):
    from api import main

    assert RuntimeConfig().semantic_validation_enabled is True
    engine = NativeMedAgentEngine(RuntimeConfig(trace_content_mode="raw", trace_dir=str(tmp_path)),
                                 llm=ScriptedLLM(["invalid JSON"]))
    monkeypatch.setattr(main, "_coordinator", engine)
    with TestClient(
        main.app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)
    ) as client:
        response = client.post(
            "/api/analyze", json={"description": "Synthetic", "question": "Explain"}
        )
        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "incomplete"
        assert result["semantic_validation"]["status"] == "uncertain"
        assert result["user_request_complete"] is False
