from __future__ import annotations

import pytest

from medagent.agents.base import WorkerResult
from medagent.context.contract import AnswerContract
from medagent.context.models import CoverageItem
from medagent.context.request_spec import RequestItem, RequestSpec
from medagent.planning.complexity import build_complexity_profile
from medagent.planning.models import Plan, Subtask
from medagent.planning.planner import (
    PlanCoverageError,
    Planner,
    _reconcile_required_deliverables,
    _required_plan_deliverables,
    apply_contract_policy,
)
from medagent.runtime.coverage import evaluate_contract_coverage, required_deliverable_ids

FOUR_REQUIRED = [
    "TREATMENT_PLAN",
    "FURTHER_TESTS",
    "DIFFERENTIAL_DIAGNOSIS",
    "DIAGNOSIS_WITH_BASIS",
]
RUN1_QUESTION = (
    "请分析最可能诊断和鉴别诊断，\n"
    "制定进一步检查方案，\n"
    "并给出治疗与随访计划。"
)


def required_contract(deliverables: list[str]) -> AnswerContract:
    must_cover = [item.replace("_", " ").lower() for item in deliverables]
    return AnswerContract(
        intent="multi_deliverable",
        requested_deliverables=deliverables,
        must_cover=must_cover,
        coverage_checklist=[CoverageItem(item=item) for item in must_cover],
    )


def run1_request_spec() -> RequestSpec:
    return RequestSpec(
        [RequestItem("RQ1", RUN1_QUESTION, True, 1, RUN1_QUESTION, "TREATMENT_PLAN")]
    )


def owners(plan: Plan) -> set[str]:
    return {item for subtask in plan.subtasks for item in subtask.deliverable_ids}


def test_run1_reassigns_further_tests_after_unneeded_research_is_pruned() -> None:
    """Reconstruct the raw Planner subtasks in fa9afaad before policy pruning."""

    contract = required_contract(FOUR_REQUIRED)
    complexity = build_complexity_profile(RUN1_QUESTION, contract)
    raw_plan = Plan(
        [
            Subtask(
                "ST1",
                "Diagnosis with basis and differential.",
                "diagnostic_agent",
                ["DIAGNOSIS_WITH_BASIS", "DIFFERENTIAL_DIAGNOSIS"],
                request_item_ids=["RQ1"],
            ),
            Subtask(
                "ST2",
                "Further diagnostic tests.",
                "research_agent",
                ["FURTHER_TESTS"],
                request_item_ids=["RQ1"],
            ),
            Subtask(
                "ST3",
                "Treatment and follow-up.",
                "consultation_agent",
                ["TREATMENT_PLAN"],
                request_item_ids=["RQ1"],
            ),
        ]
    )

    normalized = apply_contract_policy(raw_plan, contract, complexity, run1_request_spec())

    assert "removed_unneeded_research:ST2" in normalized.policy_actions
    assert [item.assigned_agent for item in normalized.subtasks] == [
        "diagnostic_agent",
        "consultation_agent",
    ]
    assert set(required_deliverable_ids(contract)) <= owners(normalized)
    assert "FURTHER_TESTS" in normalized.subtasks[0].deliverable_ids
    assert "FURTHER_TESTS" not in normalized.subtasks[1].deliverable_ids
    assert "required_deliverable_reassigned:FURTHER_TESTS:ST1" in normalized.policy_actions


def test_single_surviving_consultation_worker_owns_all_required_deliverables() -> None:
    contract = required_contract(
        ["DIAGNOSIS_WITH_BASIS", "FURTHER_TESTS", "TREATMENT_PLAN"]
    )
    complexity = build_complexity_profile("Diagnosis, further tests, and treatment.", contract)
    raw_plan = Plan(
        [Subtask("only", "Clinical assessment.", "consultation_agent", ["TREATMENT_PLAN"])]
    )

    normalized = apply_contract_policy(raw_plan, contract, complexity)

    assert len(normalized.subtasks) == 1
    assert set(required_deliverable_ids(contract)) <= owners(normalized)
    assert len(normalized.subtasks[0].deliverable_ids) == len(
        set(normalized.subtasks[0].deliverable_ids)
    )


def test_matching_request_item_takes_priority_over_other_role() -> None:
    contract = required_contract(
        ["DIAGNOSIS_WITH_BASIS", "FURTHER_TESTS", "TREATMENT_PLAN"]
    )
    complexity = build_complexity_profile("Diagnosis, further tests, and treatment.", contract)
    request_spec = RequestSpec(
        [
            RequestItem(
                "RQ1", "Give the diagnosis.", True, 1, "Give the diagnosis.",
                "DIAGNOSIS_WITH_BASIS",
            ),
            RequestItem(
                "RQ2",
                "Recommend further tests and treatment.",
                True,
                2,
                "Recommend further tests and treatment.",
                "TREATMENT_PLAN",
            ),
        ]
    )
    raw_plan = Plan(
        [
            Subtask(
                "diagnosis", "Diagnose.", "diagnostic_agent", ["DIAGNOSIS_WITH_BASIS"],
                request_item_ids=["RQ1"],
            ),
            Subtask(
                "management", "Manage.", "consultation_agent", ["TREATMENT_PLAN"],
                request_item_ids=["RQ2"],
            ),
        ]
    )

    normalized = apply_contract_policy(raw_plan, contract, complexity, request_spec)

    assert "FURTHER_TESTS" in normalized.subtasks[1].deliverable_ids
    assert "FURTHER_TESTS" not in normalized.subtasks[0].deliverable_ids
    assert [item.request_item_ids for item in normalized.subtasks] == [["RQ1"], ["RQ2"]]


@pytest.mark.parametrize("fallback_kind", ["structured_parse", "provider_request"])
def test_planner_fallback_uses_same_reconciliation(fallback_kind: str) -> None:
    contract = required_contract(FOUR_REQUIRED)
    complexity = build_complexity_profile(RUN1_QUESTION, contract)
    if fallback_kind == "structured_parse":
        fallback = Planner.parse("not JSON", "consultation_agent")
    else:
        fallback = Plan(
            [Subtask("task-1", RUN1_QUESTION, "consultation_agent")],
            "planner_request_fallback:ConnectError",
            planner_parse_status="fallback",
            planner_generation_status="provider_error",
        )
    assert fallback.fallback_reason is not None

    normalized = apply_contract_policy(fallback, contract, complexity, run1_request_spec())

    assert len(normalized.subtasks) == 1
    assert normalized.subtasks[0].assigned_agent == "consultation_agent"
    assert set(required_deliverable_ids(contract)) <= owners(normalized)
    assert normalized.fallback_reason == fallback.fallback_reason


def test_reconciliation_does_not_mark_a_failed_worker_complete() -> None:
    contract = required_contract(["FURTHER_TESTS"])
    complexity = build_complexity_profile("Further tests?", contract)
    plan = apply_contract_policy(
        Plan([Subtask("tests", "Recommend tests.", "diagnostic_agent", ["FURTHER_TESTS"])]),
        contract,
        complexity,
    )
    failed_worker = WorkerResult("diagnostic_agent", "tests", "", success=False)

    coverage = evaluate_contract_coverage(contract, plan.subtasks, [failed_worker])

    assert "FURTHER_TESTS" in owners(plan)
    assert coverage.covered_deliverable_ids == []
    assert coverage.missing_required_deliverables == ["FURTHER_TESTS"]


def test_complete_plan_is_idempotent_and_keeps_existing_ownership() -> None:
    contract = required_contract(
        ["DIAGNOSIS_WITH_BASIS", "FURTHER_TESTS", "TREATMENT_PLAN"]
    )
    complexity = build_complexity_profile("Diagnosis, further tests, and treatment.", contract)
    original = Plan(
        [
            Subtask(
                "diagnosis",
                "Diagnosis and tests.",
                "diagnostic_agent",
                ["DIAGNOSIS_WITH_BASIS", "FURTHER_TESTS"],
            ),
            Subtask(
                "treatment",
                "Treatment.",
                "consultation_agent",
                ["TREATMENT_PLAN"],
            ),
        ]
    )

    first = apply_contract_policy(original, contract, complexity)
    second = apply_contract_policy(first, contract, complexity)

    assert [item.to_dict() for item in first.subtasks] == [
        item.to_dict() for item in second.subtasks
    ]
    assert len(first.subtasks) == len(original.subtasks)
    assert not any(
        action.startswith("required_deliverable_reassigned:")
        for action in first.policy_actions + second.policy_actions
    )


def test_reconciliation_rejects_a_plan_with_no_surviving_owner() -> None:
    contract = required_contract(["FURTHER_TESTS"])
    complexity = build_complexity_profile("Further tests?", contract)

    with pytest.raises(PlanCoverageError, match="FURTHER_TESTS"):
        _reconcile_required_deliverables([], contract, complexity, None)


def test_required_owner_selection_matches_unchanged_contract_gate() -> None:
    contract = required_contract(["DIAGNOSIS_WITH_BASIS", "FURTHER_TESTS"])
    contract.requested_deliverables.append("TREATMENT_PLAN")
    contract.optional_cover.append("treatment plan")
    contract.coverage_checklist.append(CoverageItem("treatment plan", priority="OPTIONAL"))

    assert _required_plan_deliverables(contract) == required_deliverable_ids(contract)
    assert _required_plan_deliverables(contract) == [
        "DIAGNOSIS_WITH_BASIS",
        "FURTHER_TESTS",
    ]
