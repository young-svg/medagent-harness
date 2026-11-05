"""Review counterexamples for program constraints, not model-accuracy claims."""
import json

import pytest

from medagent.context.contract import AnswerContract
from medagent.context.evidence_ledger import build_evidence_ledger
from medagent.context.models import CoverageItem
from medagent.guardrails.contract_checker import CheckResult, Guardrail
from medagent.guardrails.stable_patch import EditAction, StableEdit
from medagent.llm.fakes import DeterministicLLM, ScriptedLLM
from medagent.memory.session import SessionMemory
from medagent.observability.replay import read_trace
from medagent.observability.tracer import TraceRecorder
from medagent.runtime.config import RuntimeConfig
from medagent.runtime.coverage import required_deliverable_ids
from medagent.runtime.native_engine import NativeMedAgentEngine
from medagent.runtime.semantic_validation import SemanticFailure, SemanticValidator, parse_inventory
from tests.test_semantic_validation import inventory, verdict


@pytest.mark.parametrize("question", [
    "为什么不需要抗生素？", "请解释为什么不需要抗生素。",
    "Explain why patients do not need antibiotics.",
    "Explain whether this test is optional.", "请解释这个检查是否可选。",
    "请解释‘不要提供治疗方案’是什么意思。",
])
def test_propositions_with_scope_words_remain_required(question):
    parsed = parse_inventory(inventory(question, [(question, "required")]), question)
    assert parsed == [{"start": 0, "end": len(question), "kind": "required"}]


@pytest.mark.parametrize("question,kind", [
    ("不要提供治疗方案。", "prohibited"), ("请不要提供治疗方案。", "prohibited"),
    ("Do not provide a treatment plan.", "prohibited"),
    ("Please do not provide a treatment plan.", "prohibited"),
    ("可选：说明治疗方案。", "optional"), ("optional: discuss treatment.", "optional"),
])
def test_direct_scope_cannot_be_reclassified_as_required(question, kind):
    assert parse_inventory(inventory(question, [(question, kind)]), question)
    with pytest.raises(ValueError, match="scope qualifier"):
        parse_inventory(inventory(question, [(question, "required")]), question)


def test_proposition_and_direct_prohibition_in_same_question():
    question = "为什么不需要抗生素？不要提供治疗方案。"
    correct = inventory(question, [("为什么不需要抗生素？", "required"),
                                   ("不要提供治疗方案。", "prohibited")])
    assert [item["kind"] for item in parse_inventory(correct, question)] == [
        "required", "prohibited",
    ]
    with pytest.raises(ValueError, match="scope qualifier"):
        parse_inventory(inventory(question, [(question, "required")]), question)


def priority_contract(checklist=False):
    return AnswerContract(
        "multi_deliverable", ["DIAGNOSIS_WITH_BASIS", "TREATMENT_PLAN"],
        must_cover=[] if checklist else ["diagnosis with basis"],
        optional_cover=["treatment plan"],
        coverage_checklist=[CoverageItem("diagnosis with basis", "MUST"),
                            CoverageItem("treatment plan", "OPTIONAL")] if checklist else [],
    )


@pytest.mark.parametrize("checklist", [True, False])
def test_real_guardrail_and_coverage_agree_on_optional_omission(checklist):
    contract = priority_contract(checklist)
    answer = "Diagnosis with basis: provisional synthetic finding."
    result = Guardrail().check(answer, contract, build_evidence_ledger("Synthetic"))
    assert required_deliverable_ids(contract) == contract.required_deliverable_ids == [
        "DIAGNOSIS_WITH_BASIS",
    ]
    assert "required_deliverable_ids" not in contract.to_dict()  # No serialized protocol change.
    assert result.passed and result.issues == []
    missing = Guardrail().check("Unrelated text.", contract, build_evidence_ledger("Synthetic"))
    assert missing.missing == ["DIAGNOSIS_WITH_BASIS"]


def test_optional_omission_does_not_suppress_real_safety_contradiction():
    result = Guardrail().check(
        "Diagnosis with basis: Disease A is confirmed.", priority_contract(),
        build_evidence_ledger("No disease A"),
    )
    assert result.missing == []
    assert any(issue.startswith("contradiction:") for issue in result.issues)
    assert len(result.edits) == 1


QUESTION = "1. Explain causes.\n2. List warning signs."
OUTCOMES = [("Explain causes.", "required"), ("List warning signs.", "required")]
TEXTS = ["Causes include synthetic Alpha.", "Signs include synthetic Beta."]


def business_responses():
    return [
        {"subtasks": [{"description": "Address both source requests",
                       "assigned_agent": "diagnostic_agent", "request_item_ids": ["RQ1", "RQ2"]}]},
        {"answers": [{"request_item_id": f"RQ{i}", "answer": text}
                     for i, text in enumerate(TEXTS, 1)]},
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("scenario", ["initial", "final", "recovery", "rejected_patch"])
async def test_hard_block_survives_all_completion_paths(tmp_path, monkeypatch, enabled, scenario):
    calls = []

    def check(self, answer, *args):
        calls.append(answer)
        first = len(calls) == 1
        if scenario == "recovery" and first:
            return CheckResult(False, ["explicit_policy_block"],
                               [StableEdit("U2", EditAction.DELETE)], ["explicit_policy_block"])
        if scenario == "rejected_patch" and first:
            return CheckResult(False, ["unsafe_patch"], [StableEdit("absent", EditAction.DELETE)])
        if (scenario == "initial" and first) or (scenario == "final" and not first):
            return CheckResult(False, ["explicit_policy_block"],
                               blocking_issues=["explicit_policy_block"])
        return CheckResult(True)

    monkeypatch.setattr(Guardrail, "check", check)
    worker = ScriptedLLM(business_responses())
    extraction = inventory(QUESTION, OUTCOMES)
    checker = ScriptedLLM([extraction, extraction, verdict(TEXTS)])
    engine = NativeMedAgentEngine(
        RuntimeConfig(
            trace_content_mode="raw",
            trace_dir=str(tmp_path), semantic_validation_enabled=enabled),
        llm=worker, validation_llm=checker,
    )
    try:
        result = await engine.analyze("Synthetic", QUESTION)
        assert not engine.memory._messages  # Failed acceptance must not be stored as success.
    finally:
        await engine.close()
    assert result["status"] == "incomplete"
    assert result["structural_user_request_complete"] is True
    assert result["user_request_complete"] is False
    assert result["trace"]["status"] == "incomplete"
    assert result["semantic_validation"]["status"] == "blocked"
    assert len(checker.requests) == (2 if enabled else 0)
    assert len(worker.requests) == 2
    expected = "safety_patch_rejected" if scenario == "rejected_patch" else "explicit_policy_block"
    assert result["semantic_validation"]["blocking_issues"] == [expected]
    events = read_trace(tmp_path / result["run_id"])
    final_check = [e["payload"] for e in events if e["event_type"] == "checker_result"][-1]
    assert final_check["blocking_issues"] == [expected] and not final_check["passed"]
    if scenario == "recovery":
        assert len(calls) == 3  # Two edit/recovery passes and the final public-text check.
        assert result["final_request_item_answers"]["RQ2"] == TEXTS[1]
    assert "presentation_transform" not in result["trace"]["events"]


@pytest.mark.asyncio
async def test_validator_hard_block_does_not_spend_even_one_call(tmp_path):
    from medagent.context.request_spec import build_request_spec
    from medagent.runtime.semantic_validation import contract_for_spec

    llm = ScriptedLLM([])
    gate = SemanticValidator(llm, RuntimeConfig(), TraceRecorder(str(tmp_path), content_mode="raw"))
    spec = build_request_spec("Explain causes")
    result = await gate.accept(
        question="Explain causes", description="Synthetic", spec=spec,
        contract=contract_for_spec(spec, []), restrictions=[], answers={"RQ1": TEXTS[0]},
        guardrail=CheckResult(False, blocking_issues=["hard_policy"]),
    )
    assert not result["complete"] and not llm.requests


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["bounded", "zero_budget", "disabled"])
async def test_validators_share_existing_bounded_memory_snapshot(tmp_path, mode):
    memory = SessionMemory(recent_limit=3, token_budget=0 if mode == "zero_budget" else 4096)
    memory.add("active", "user", "OLD_TRIMMED_TASK: provide treatment.")
    memory.add("active", "user", "Earlier alternatives: Alpha or Beta; provide treatment.")
    memory.add("active", "assistant", "Alpha is noninvasive; Beta requires a procedure.")
    memory.add("other", "user", "OTHER_SESSION_SECRET")
    if mode == "disabled":
        memory.enabled = False
    question = "Compare the two previous options. Do not provide treatment."
    description = "Current request forbids a treatment plan."
    expected = memory.context("active", f"{description}\nQuestion: {question}")[:-1]
    extraction = inventory(question, [("Compare the two previous options.", "required"),
                                      ("Do not provide treatment.", "prohibited")])
    text = "Alpha avoids a procedure; Beta involves a procedure."
    checker = ScriptedLLM([extraction, extraction, verdict([text], restrictions=["satisfied"])])
    worker = ScriptedLLM([
        {"subtasks": [{"description": "Compare prior options only",
                       "assigned_agent": "diagnostic_agent", "request_item_ids": ["RQ1"]}]},
        text,
    ])
    engine = NativeMedAgentEngine(RuntimeConfig(
        trace_content_mode="raw",
        trace_dir=str(tmp_path)), llm=worker,
                                 validation_llm=checker, memory=memory)
    result = await engine.analyze(description, question, "active")
    await engine.close()
    assert result["status"] == "completed"
    assert len(checker.requests) == 3
    for request in checker.requests:
        payload = json.loads(request["messages"][1]["content"])
        assert payload["bounded_session_context"] == expected
        assert payload["question"] == question and payload["case"] == description
        assert "OTHER_SESSION_SECRET" not in request["messages"][1]["content"]
        assert "OLD_TRIMMED_TASK" not in request["messages"][1]["content"]
        assert "Current question" in request["messages"][0]["content"]
    assert len(expected) == (2 if mode == "bounded" else 0)
    # No historical task may become a new RQ or a required clinical deliverable.
    worker_payload = json.loads(worker.requests[1]["messages"][1]["content"])
    assert worker_payload["bounded_session_context"] == expected
    assert len(worker_payload["request_spec"]["items"]) == 1
    assert "TREATMENT_PLAN" not in worker_payload["contract"]["requested_deliverables"]
    events = read_trace(tmp_path / result["run_id"])
    history_event = next(e for e in events if e["event_type"] == "memory_read")
    assert history_event["payload"]["injected_messages"] == expected


@pytest.mark.asyncio
async def test_validator_copies_history_and_never_accepts_history_offsets(tmp_path):
    history = [{"role": "user", "content": "Earlier background"}]
    question = "Explain causes"
    extraction = inventory(question, [(question, "required")])
    llm = ScriptedLLM([extraction, extraction])
    gate = SemanticValidator(llm, RuntimeConfig(), TraceRecorder(str(tmp_path), content_mode="raw"),
                             memory_context=history)
    history[0]["content"] = "Changed after read"
    await gate.extract(question)
    assert json.loads(llm.requests[0]["messages"][1]["content"])["bounded_session_context"] == [
        {"role": "user", "content": "Earlier background"},
    ]
    bad = {"segments": [{"start": 0, "end": 999, "kind": "required"}]}
    with pytest.raises(ValueError, match="source intervals"):
        parse_inventory(bad, question)


@pytest.mark.asyncio
async def test_history_over_validation_input_limit_fails_closed_without_truncation(tmp_path):
    llm = ScriptedLLM([])
    gate = SemanticValidator(
        llm, RuntimeConfig(semantic_max_input_chars=200), TraceRecorder(
            str(tmp_path), content_mode="raw"),
        memory_context=[{"role": "user", "content": "H" * 300}],
    )
    with pytest.raises(SemanticFailure, match="semantic_input_budget_exceeded"):
        await gate.extract("Explain")
    assert not llm.requests


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
async def test_local_offline_demo_has_explicit_compatibility_mode(tmp_path, enabled):
    engine = NativeMedAgentEngine(
        RuntimeConfig(
            trace_content_mode="raw",
            trace_dir=str(tmp_path), semantic_validation_enabled=enabled),
        llm=DeterministicLLM(),
    )
    result = await engine.analyze("Synthetic case", "What should be assessed?")
    await engine.close()
    if enabled:
        assert result["status"] == "incomplete"
        assert result["semantic_validation"]["status"] == "uncertain"
        assert "plan_created" not in result["trace"]["events"]
        assert result["trace"]["llm_calls"] == 1
    else:
        assert result["status"] == "completed"
        assert result["semantic_validation"]["status"] == "disabled"
        assert result["semantic_validation"]["assurance"] == "structural_only"
