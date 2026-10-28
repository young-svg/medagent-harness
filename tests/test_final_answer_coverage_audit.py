"""A2 final-answer coverage, recovery, safety, and model-call regressions."""

from __future__ import annotations

import asyncio

import pytest

from api.schemas import AnalyzeResponse
from medagent.context.contract import AnswerContract, build_answer_contract
from medagent.context.evidence_ledger import EvidenceLedger
from medagent.guardrails.contract_checker import Guardrail
from medagent.guardrails.stable_patch import EditAction, StableEdit
from medagent.llm import ScriptedLLM
from medagent.observability.replay import read_trace, replay
from medagent.retrieval.backend import FakeRetrievalBackend
from medagent.runtime.config import RuntimeConfig
from medagent.runtime.native_engine import NativeMedAgentEngine

QUESTION = "1. Provide the diagnosis with basis.\n2. Provide the treatment plan."
DIAGNOSIS = "Diagnosis with basis: Synthetic condition Alpha is suggested by observation Zeta."
CARE = "Treatment plan: Follow clinician-supervised protocol Beta and reassess in seven days."
BENEFIT = "The practical benefit is fewer scheduled visits."
COMPLETE = f"{DIAGNOSIS}\n\n{CARE}"
PARAPHRASE = (
    "The provisional explanation is synthetic condition Alpha, supported by observation Zeta."
    "\n\nFor care, follow clinician-supervised protocol Beta and reassess in seven days."
)


def _answer(*pairs):
    return {
        "answers": [
            {"request_item_id": request_id, "answer": answer} for request_id, answer in pairs
        ]
    }


def _run(
    tmp_path,
    synthesis,
    *,
    benefit=False,
    optional_benefit=False,
    care=CARE,
    single=False,
    early=False,
):
    question = QUESTION
    care_ids = ["RQ2"]
    care_pairs = [("RQ2", care)]
    if benefit:
        question += "\n3. " + ("Optional: " if optional_benefit else "")
        question += "What is the main benefit of this approach?"
        care_ids.append("RQ3")
        care_pairs.append(("RQ3", BENEFIT))
    plan = {
        "subtasks": [
            {
                "subtask_id": "diagnosis",
                "description": "Explain the provisional condition and its basis.",
                "assigned_agent": "diagnostic_agent",
                "deliverable_ids": ["DIAGNOSIS_WITH_BASIS"],
                "request_item_ids": ["RQ1"],
            },
            {
                "subtask_id": "care",
                "description": "Outline the requested care and benefit.",
                "assigned_agent": "consultation_agent",
                "deliverable_ids": ["TREATMENT_PLAN"],
                "request_item_ids": care_ids,
            },
        ]
    }
    if early:
        # Required ownership is reconciled by the release Planner. Keep a real
        # treatment owner that fails, while the diagnostic Worker answers both
        # RequestItems: Worker request coverage can still precede an early return.
        plan["subtasks"][0]["request_item_ids"] = ["RQ1", "RQ2"]
        responses = [plan, _answer(("RQ1", DIAGNOSIS), ("RQ2", care)), ""]
    elif single:
        plan["subtasks"] = [
            {
                "subtask_id": "single",
                "description": "Answer the request.",
                "assigned_agent": "consultation_agent",
                "deliverable_ids": ["TREATMENT_PLAN"],
                "request_item_ids": ["RQ1"],
            }
        ]
        question = "Provide the treatment plan."
        responses = [plan, _answer(("RQ1", care))]
    else:
        responses = [plan, _answer(("RQ1", DIAGNOSIS)), _answer(*care_pairs), synthesis]
    llm = ScriptedLLM(responses)
    engine = NativeMedAgentEngine(
        RuntimeConfig(trace_dir=str(tmp_path)),
        llm=llm,
        retrieval_backend=FakeRetrievalBackend([]),
    )

    async def analyze():
        try:
            return await engine.analyze("Synthetic vignette: observation Zeta.", question, "a2")
        finally:
            await engine.close()

    result = asyncio.run(analyze())
    events = read_trace(tmp_path / result["run_id"])
    assert len(llm.requests) == (2 if single else 3 if early else 4)
    assert result["trace"]["llm_calls"] == len(llm.requests)
    assert result["trace"]["tool_calls"] == 0
    assert not llm.responses
    assert result["presentation"]["professional_answer"] == result["final_answer"]
    assert result["final_answer"] == "\n\n".join(result["final_request_item_answers"].values())
    api_result = AnalyzeResponse(**result).model_dump()
    assert api_result["user_request_complete"] == result["trace"]["user_request_complete"]
    assert api_result["final_request_item_answers"] == result["final_request_item_answers"]
    execution = result["presentation"]["execution_summary"]
    assert execution["user_request_complete"] == result["user_request_complete"]
    assert (
        execution["final_request_coverage"]["request_item_answers"]
        == result["final_request_item_answers"]
    )
    return result, events, llm


def _payload(events, event_type):
    return next(event["payload"] for event in events if event["event_type"] == event_type)


@pytest.mark.parametrize(
    ("synthesis", "benefit", "expected", "recovered", "guardrail_missing"),
    [
        pytest.param(
            _answer(("RQ1", DIAGNOSIS)),
            False,
            COMPLETE,
            ["RQ2"],
            set(),
            id="synthesis-omits-required-care",
        ),
        pytest.param(
            _answer(("RQ1", DIAGNOSIS), ("RQ2", CARE)),
            False,
            COMPLETE,
            [],
            set(),
            id="complete-canonical-answer",
        ),
        pytest.param(
            _answer(*zip(["RQ1", "RQ2"], PARAPHRASE.split("\n\n"), strict=True)),
            False,
            PARAPHRASE,
            [],
            {"DIAGNOSIS_WITH_BASIS", "TREATMENT_PLAN"},
            id="complete-paraphrase-false-alarm",
        ),
        pytest.param(
            _answer(("RQ1", DIAGNOSIS), ("RQ2", "Treatment plan:")),
            False,
            COMPLETE,
            ["RQ2"],
            set(),
            id="empty-care-heading",
        ),
        pytest.param(
            _answer(("RQ1", DIAGNOSIS), ("RQ2", CARE)),
            True,
            f"{COMPLETE}\n\n{BENEFIT}",
            ["RQ3"],
            set(),
            id="unknown-required-benefit",
        ),
        pytest.param(
            _answer(("RQ2", CARE), ("RQ1", DIAGNOSIS)),
            False,
            COMPLETE,
            [],
            set(),
            id="render-in-request-order",
        ),
        pytest.param(
            _answer(("RQ1", "模拟情况甲与观察结果相符。"), ("RQ2", "遵循既定方案并在七天后复查。")),
            False,
            "模拟情况甲与观察结果相符。\n\n遵循既定方案并在七天后复查。",
            [],
            {"DIAGNOSIS_WITH_BASIS", "TREATMENT_PLAN"},
            id="complete-chinese-answer",
        ),
        pytest.param(
            _answer(
                ("RQ1", DIAGNOSIS), ("RQ2", "**Follow protocol Beta and reassess in seven days.**")
            ),
            False,
            DIAGNOSIS + "\n\n**Follow protocol Beta and reassess in seven days.**",
            [],
            {"TREATMENT_PLAN"},
            id="bold-prose-is-not-a-title",
        ),
    ],
)
def test_synthesis_preserves_required_answers(
    tmp_path, synthesis, benefit, expected, recovered, guardrail_missing
):
    result, events, llm = _run(tmp_path, synthesis, benefit=benefit)
    worker = _payload(events, "request_coverage")
    final = _payload(events, "final_request_coverage")
    assert worker["user_request_complete"] is True
    assert worker["missing_request_items"] == []
    assert final["missing_request_items"] == []
    assert final["recovered_request_items"] == recovered
    assert result["final_answer"] == expected
    assert list(result["final_request_item_answers"]) == (
        ["RQ1", "RQ2", "RQ3"] if benefit else ["RQ1", "RQ2"]
    )
    assert result["status"] == result["trace"]["status"] == "completed"
    assert result["user_request_complete"] is result["trace"]["user_request_complete"] is True
    assert result["contract_complete"] is True
    assert result["missing_required_request_items"] == []
    assert result["missing_required_deliverables"] == []
    check = _payload(events, "checker_result")
    assert set(check["issues"]) == {f"missing_deliverable:{item}" for item in guardrail_missing}
    assert check["passed"] is (not guardrail_missing)
    assert check["edits"] == []
    if expected == PARAPHRASE:
        assert "diagnosis" not in expected.casefold()
        assert "treatment" not in expected.casefold()
        assert result["final_request_item_answers"] != result["request_item_answers"]
    if benefit:
        assert _payload(events, "request_spec_built")["items"][-1]["semantic_type"] == "UNKNOWN"
    assert llm.requests[-1]["response_format"] == {"type": "json_object"}
    assert "request_item_id" in llm.requests[-1]["messages"][0]["content"]
    assert _payload(events, "synthesis_input")["worker_drafts"]
    types = [event["event_type"] for event in events]
    assert types.index("request_coverage") < types.index("synthesis_output")
    assert types.index("patch_applied") < types.index("final_request_coverage")


@pytest.mark.parametrize(
    ("synthesis", "parse_status"),
    [
        ("not JSON", "invalid_json"),
        ("", "invalid_json"),
        ('{"answers": [', "invalid_json"),
        ('{"answers":[],"answers":[]}', "invalid_json"),
        ("[]", "invalid_structure"),
        ({"answers": {}}, "invalid_structure"),
        ({"answers": [], "unexpected": True}, "invalid_structure"),
        ({"answers": ["bad"]}, "invalid_structure"),
        (
            {"answers": [{"request_item_id": "RQ1", "answer": DIAGNOSIS, "extra": True}]},
            "invalid_structure",
        ),
        (_answer(("RQ9", "Untrusted content")), "invalid_request_id"),
        (_answer((None, "Untrusted content")), "invalid_request_id"),
        (_answer(("RQ1", 7)), "invalid_structure"),
        (_answer(("RQ1", None)), "invalid_structure"),
        (_answer(("RQ1", ""), ("RQ1", DIAGNOSIS)), "duplicate_request_id"),
        (
            _answer(("RQ1", DIAGNOSIS), ("RQ1", "Conflicting untrusted content")),
            "duplicate_request_id",
        ),
    ],
)
def test_invalid_synthesis_uses_existing_worker_answers(tmp_path, synthesis, parse_status):
    result, events, _ = _run(tmp_path, synthesis)
    assert result["status"] == "completed"
    assert result["final_answer"] == COMPLETE
    final = _payload(events, "final_request_coverage")
    assert final["parse_status"] == parse_status
    assert final["recovered_request_items"] == ["RQ1", "RQ2"]
    assert result["final_request_item_answers"] == result["request_item_answers"]


@pytest.mark.parametrize(
    "content",
    [
        "",
        "   ",
        "Treatment plan:",
        "# Treatment plan",
        "**Treatment plan**",
        "__Treatment plan__",
        "Treatment plan\n--------------",
        "Treatment plan\n==============",
        "Treatment\nplan\n---",
        "**Treatment plan**\n---",
        "## Treatment plan\n\n---",
        "<analysis>Hidden material</analysis>",
    ],
)
def test_empty_or_sanitized_synthesis_section_is_backfilled(tmp_path, content):
    result, events, _ = _run(tmp_path, _answer(("RQ1", DIAGNOSIS), ("RQ2", content)))
    assert result["final_answer"] == COMPLETE
    assert _payload(events, "final_request_coverage")["recovered_request_items"] == ["RQ2"]


def _delete_care(monkeypatch, predicate, replacement=None):
    original = Guardrail.check

    def check(self, answer, contract, ledger):
        result = original(self, answer, contract, ledger)
        from medagent.guardrails.stable_patch import StableDraft

        for unit in StableDraft.parse(answer).units:
            if predicate(unit.text):
                result.edits.append(
                    StableEdit(
                        unit.id,
                        EditAction.DELETE if replacement is None else EditAction.REPLACE,
                        replacement or "",
                    )
                )
                result.passed = False
        return result

    monkeypatch.setattr(Guardrail, "check", check)


@pytest.mark.parametrize("replacement", [None, "<analysis>Hidden material</analysis>", "Care:"])
def test_content_removed_by_patch_is_recovered_and_checked_again(
    tmp_path, monkeypatch, replacement
):
    _delete_care(monkeypatch, lambda text: "synth-only" in text, replacement)
    synthesized = CARE + " synth-only"
    result, events, _ = _run(tmp_path, _answer(("RQ1", DIAGNOSIS), ("RQ2", synthesized)))
    assert result["final_answer"] == COMPLETE
    assert result["status"] == "completed"
    assert _payload(events, "final_request_coverage")["recovered_request_items"] == ["RQ2"]
    checks = [event["payload"] for event in events if event["event_type"] == "checker_input"]
    assert len(checks) == 2
    assert CARE in checks[-1]["draft"] and "synth-only" not in checks[-1]["draft"]


@pytest.mark.parametrize("single", [False, True])
def test_unrecoverable_patch_loss_is_incomplete(tmp_path, monkeypatch, single):
    _delete_care(monkeypatch, lambda text: "Beta" in text)
    result, events, _ = _run(tmp_path, _answer(("RQ1", DIAGNOSIS), ("RQ2", CARE)), single=single)
    missing = ["RQ1"] if single else ["RQ2"]
    assert result["status"] == result["trace"]["status"] == "incomplete"
    assert result["user_request_complete"] is result["trace"]["user_request_complete"] is False
    assert result["trace"]["worker_user_request_complete"] is True
    assert result["missing_required_request_items"] == missing
    assert result["missing_required_deliverables"] == ["TREATMENT_PLAN"]
    assert result["contract_complete"] is result["trace"]["contract_complete"] is False
    assert "Beta" not in result["final_answer"]
    assert _payload(events, "request_coverage")["user_request_complete"] is True
    assert _payload(events, "final_request_coverage")["missing_request_items"] == missing
    final = _payload(events, "final_request_coverage")
    assert final["fallback_attempted_request_items"] == missing
    assert final["recovered_request_items"] == []
    assert not any(event["event_type"] == "memory_write" for event in events)
    replayed = replay(tmp_path / result["run_id"])
    assert replayed["summary"]["user_request_complete"] is False
    assert replayed["summary"]["missing_required_request_items"] == missing
    assert replayed["summary"]["missing_required_deliverables"] == ["TREATMENT_PLAN"]


@pytest.mark.parametrize(
    "care",
    [
        "<analysis>Only hidden material</analysis>",
        "Treatment plan:",
        "**Treatment plan**",
        "Treatment plan\n--------------",
    ],
)
def test_unusable_worker_fallback_cannot_claim_completion(tmp_path, care):
    result, events, _ = _run(tmp_path, _answer(("RQ1", DIAGNOSIS)), care=care)
    assert result["status"] == "incomplete"
    assert result["user_request_complete"] is False
    assert result["missing_required_request_items"] == ["RQ2"]
    assert "Only hidden material" not in result["final_answer"]
    assert _payload(events, "request_coverage")["user_request_complete"] is True


def test_unknown_item_loss_after_patch_remains_incomplete(tmp_path, monkeypatch):
    _delete_care(monkeypatch, lambda text: "fewer scheduled visits" in text)
    result, events, _ = _run(
        tmp_path, _answer(("RQ1", DIAGNOSIS), ("RQ2", CARE), ("RQ3", BENEFIT)), benefit=True
    )
    assert result["status"] == "incomplete"
    assert result["missing_required_request_items"] == ["RQ3"]
    assert result["user_request_complete"] is False
    assert _payload(events, "final_request_coverage")["missing_request_items"] == ["RQ3"]


@pytest.mark.parametrize(
    "care",
    [
        CARE,
        "Follow protocol Beta and reassess in seven days.",
        "**Follow protocol Beta and reassess in seven days.**",
        "__Follow protocol Beta and reassess in seven days.__",
        "**遵循既定方案并在七天后复查。**",
        "**Treatment plan**\n\nFollow protocol Beta and reassess in seven days.",
    ],
)
def test_single_worker_keeps_text_and_bypasses_synthesis(tmp_path, care):
    result, events, _ = _run(tmp_path, None, single=True, care=care)
    assert result["status"] == "completed"
    assert result["final_answer"] == care
    assert result["final_request_item_answers"] == {"RQ1": care}
    assert not any(event["event_type"] == "synthesis_input" for event in events)


def test_early_return_keeps_worker_coverage_but_reports_empty_final(tmp_path):
    result, events, _ = _run(tmp_path, None, early=True)
    worker = _payload(events, "request_coverage")
    assert worker["user_request_complete"] is True
    assert worker["covered_request_items"] == ["RQ1", "RQ2"]
    assert result["request_item_answers"] == {"RQ1": DIAGNOSIS, "RQ2": CARE}
    assert _payload(events, "contract_coverage")["missing_required_deliverables"] == [
        "TREATMENT_PLAN"
    ]
    assert result["status"] == result["trace"]["status"] == "incomplete"
    assert result["final_answer"] == ""
    assert result["final_request_item_answers"] == {}
    assert result["user_request_complete"] is result["trace"]["user_request_complete"] is False
    assert result["contract_complete"] is result["trace"]["contract_complete"] is False
    assert result["trace"]["worker_user_request_complete"] is True
    assert result["missing_required_request_items"] == ["RQ1", "RQ2"]
    final = _payload(events, "final_request_coverage")
    assert final["request_item_answers"] == {}
    assert final["covered_request_items"] == []
    assert final["missing_request_items"] == ["RQ1", "RQ2"]
    assert final["fallback_attempted_request_items"] == final["recovered_request_items"] == []
    execution = result["presentation"]["execution_summary"]
    assert execution["request_coverage"] == worker
    assert execution["final_request_coverage"] == {key: final[key] for key in worker}
    replayed = replay(tmp_path / result["run_id"])
    assert replayed["final_answer"] == ""
    assert replayed["summary"]["user_request_complete"] is False
    assert replayed["summary"]["missing_required_request_items"] == ["RQ1", "RQ2"]
    assert not any(event["event_type"] in {"synthesis_input", "memory_write"} for event in events)


def test_only_retained_fallbacks_are_recovered(tmp_path, monkeypatch):
    _delete_care(monkeypatch, lambda text: "Beta" in text)
    result, events, _ = _run(tmp_path, _answer(("RQ1", DIAGNOSIS)), benefit=True)
    final = _payload(events, "final_request_coverage")
    assert result["status"] == "incomplete"
    assert result["final_answer"] == DIAGNOSIS + "\n\n" + BENEFIT
    assert result["final_request_item_answers"] == {"RQ1": DIAGNOSIS, "RQ3": BENEFIT}
    assert final["fallback_attempted_request_items"] == ["RQ2", "RQ3"]
    assert final["recovered_request_items"] == ["RQ3"]
    assert final["missing_request_items"] == ["RQ2"]


def test_markdown_and_patch_preserve_request_unit_ownership(tmp_path, monkeypatch):
    revised = DIAGNOSIS + " Reviewed."
    _delete_care(monkeypatch, lambda text: "Alpha" in text, revised)
    diagnosis = (
        "Diagnosis with basis\n--------------------\n\n"
        + DIAGNOSIS
        + "\n\nDetails remain provisional."
    )
    care = "**Treatment plan**\n\n**Follow protocol Beta and reassess in seven days.**"
    result, events, _ = _run(tmp_path, _answer(("RQ1", diagnosis), ("RQ2", care)))
    expected = {"RQ1": diagnosis.replace(DIAGNOSIS, revised), "RQ2": care}
    assert result["status"] == "completed"
    assert result["final_request_item_answers"] == expected
    assert result["final_answer"] == "\n\n".join(expected.values())
    assert _payload(events, "patch_applied")["applied"] is True
    assert _payload(events, "final_request_coverage")["recovered_request_items"] == []


def test_optional_item_is_not_required_for_final_completion(tmp_path):
    result, events, _ = _run(
        tmp_path,
        _answer(("RQ1", DIAGNOSIS), ("RQ2", CARE)),
        benefit=True,
        optional_benefit=True,
    )
    assert result["status"] == "completed"
    assert result["user_request_complete"] is True
    assert result["final_answer"] == COMPLETE
    assert "RQ3" not in result["final_request_item_answers"]
    assert _payload(events, "final_request_coverage")["recovered_request_items"] == []


def test_cross_section_sanitization_cannot_keep_stale_coverage(tmp_path):
    synthesis = _answer(("RQ1", DIAGNOSIS + "<analysis>"), ("RQ2", CARE + "</analysis>"))
    result, events, _ = _run(tmp_path, synthesis)
    assert result["status"] == "completed"
    assert result["final_answer"] == COMPLETE
    # Each item is now sanitized independently before rendering, so neither
    # item's public answer needs recovery from a cross-section reasoning block.
    assert _payload(events, "final_request_coverage")["recovered_request_items"] == []


def test_guardrail_treats_optional_deliverable_as_missing():
    contract = AnswerContract(
        intent="multi_deliverable",
        requested_deliverables=["DIAGNOSIS_WITH_BASIS", "TREATMENT_PLAN"],
        must_cover=["diagnosis with basis"],
        optional_cover=["treatment plan"],
    )
    contract.validate()
    check = Guardrail().check(DIAGNOSIS, contract, EvidenceLedger())
    assert check.passed is False
    assert check.missing == ["TREATMENT_PLAN"]
    assert check.edits == []


def test_guardrail_skips_missing_check_for_single_deliverable():
    contract = build_answer_contract("Provide the treatment plan.")
    assert contract.requested_deliverables == ["TREATMENT_PLAN"]
    check = Guardrail().check(DIAGNOSIS, contract, EvidenceLedger())
    assert check.passed is True
    assert check.missing == []
