from medagent.context.contract import build_answer_contract
from medagent.context.evidence_ledger import build_evidence_ledger
from medagent.guardrails.contract_checker import Guardrail
from medagent.guardrails.stable_patch import StableDraft, apply_stable_edits


def _check(answer: str, facts: str):
    return Guardrail().check(
        answer, build_answer_contract("case analysis"), build_evidence_ledger(facts)
    )


def test_negation_for_disease_b_does_not_downgrade_disease_a() -> None:
    assert not _check("Disease A is the leading hypothesis.", "No evidence of disease B").edits


def test_recommendation_is_not_completed_treatment() -> None:
    assert not _check(
        "Treatment should be considered after review.", "Treatment not yet started"
    ).edits


def test_historical_diagnosis_is_not_current_hypothesis() -> None:
    assert not _check("History of disease A was documented in 2020.", "No current disease A").edits


def test_medical_history_question_is_not_patient_fact() -> None:
    assert not _check("Has the patient previously had disease A?", "No disease A").edits


def test_mixed_fact_question_is_detected_but_not_edited() -> None:
    result = _check("Disease A may be present; should it be confirmed?", "No disease A")
    assert not result.edits
    draft = StableDraft.parse("Disease A may be present; should it be confirmed?")
    assert draft.units[0].mixed


def test_explicit_same_diagnosis_contradiction_is_downgraded() -> None:
    result = _check("Disease A is confirmed.", "No disease A")
    assert len(result.edits) == 1
    patched = apply_stable_edits(StableDraft.parse("Disease A is confirmed."), result.edits)
    assert patched.applied
    assert patched.answer.startswith("Unconfirmed hypothesis")
