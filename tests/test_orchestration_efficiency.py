from __future__ import annotations

import asyncio
from functools import wraps
from typing import Any

import pytest

from medagent.context.contract import build_answer_contract
from medagent.context.request_spec import build_request_spec
from medagent.planning.complexity import (
    build_complexity_profile,
    build_response_profile,
    tool_capabilities,
)
from medagent.planning.models import Plan, Subtask
from medagent.planning.planner import apply_contract_policy
from medagent.planning.router import Router
from medagent.tools.registry import ToolRegistry


def async_test(function: Any) -> Any:
    @wraps(function)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return asyncio.run(function(*args, **kwargs))

    return wrapper


@pytest.mark.parametrize(
    ("question", "breadth", "external"),
    [
        ("What is the first-line treatment?", "focused", False),
        ("What is the mechanism of this treatment?", "focused", False),
        (
            "Give the most likely diagnosis, diagnostic basis, and further tests.",
            "moderate",
            False,
        ),
        (
            "Provide a comprehensive diagnosis, differential, tests, and treatment plan.",
            "comprehensive",
            False,
        ),
        ("Answer the treatment question using the relevant clinical guideline.", "moderate", True),
        ("What is the most likely diagnosis?", "focused", False),
    ],
)
def test_synthetic_complexity_profiles(
    question: str, breadth: str, external: bool
) -> None:
    contract = build_answer_contract(question)
    profile = build_complexity_profile(question, contract)
    assert profile.breadth == breadth
    assert profile.requires_external_evidence is external


def test_focused_plan_maps_to_contract_and_removes_expansion() -> None:
    contract = build_answer_contract("What is the first-line treatment?")
    profile = build_complexity_profile("What is the first-line treatment?", contract)
    proposed = Plan(
        [
            Subtask("t1", "Give treatment.", "consultation_agent", ["TREATMENT_PLAN"]),
            Subtask("t2", "Add a full differential.", "diagnostic_agent"),
            Subtask("t3", "Search the literature.", "research_agent"),
        ]
    )

    constrained = apply_contract_policy(proposed, contract, profile)

    assert len(constrained.subtasks) == 1
    assert constrained.subtasks[0].assigned_agent == "consultation_agent"
    assert constrained.subtasks[0].deliverable_ids == contract.requested_deliverables
    assert constrained.subtasks[0].justification
    assert Router().route(constrained, profile).mode == "single"


def test_generic_diagnosis_and_treatment_preserves_cross_role_plan() -> None:
    question = "Give the diagnosis and treatment plan."
    contract = build_answer_contract(question)
    profile = build_complexity_profile(question, contract)
    proposed = Plan(
        [
            Subtask(
                "d1",
                "Address diagnosis.",
                "diagnostic_agent",
                ["DIAGNOSIS_WITH_BASIS"],
            ),
            Subtask(
                "m1",
                "Address treatment.",
                "consultation_agent",
                ["TREATMENT_PLAN"],
            ),
        ]
    )

    constrained = apply_contract_policy(proposed, contract, profile)

    assert profile.breadth == "moderate"
    assert profile.requires_cross_role_reasoning
    assert not profile.requires_multi_agent
    assert len(constrained.subtasks) == 2
    assert Router().route(constrained, profile).mode == "multi"


def test_simple_gerd_request_uses_one_worker_without_retrieval() -> None:
    question = "请分析最可能诊断、诊断依据以及初步处理方案。"
    contract = build_answer_contract(question)
    profile = build_complexity_profile(question, contract)
    request_spec = build_request_spec(question)
    proposed = Plan(
        [
            Subtask(
                "d1",
                "分析最可能诊断及依据。",
                "diagnostic_agent",
                ["DIAGNOSIS_WITH_BASIS"],
                request_item_ids=["RQ1"],
            ),
            Subtask(
                "m1",
                "给出初步处理方案。",
                "consultation_agent",
                ["TREATMENT_PLAN"],
                request_item_ids=["RQ1"],
            ),
        ]
    )

    constrained = apply_contract_policy(proposed, contract, profile, request_spec)
    route = Router().route(constrained, profile)
    capabilities = tool_capabilities(profile)

    assert not profile.requires_cross_role_reasoning
    assert not profile.requires_multi_agent
    assert route.mode == "single"
    assert len(route.workers) == 1
    assert "research_agent" not in route.workers
    assert capabilities["research_agent"] == set()
    assert "clinical_guideline" not in capabilities[route.workers[0]]


def test_guideline_request_keeps_research_and_consultation_workers() -> None:
    question = "请结合相关指南说明高血压患者生活方式管理方案。"
    contract = build_answer_contract(question)
    profile = build_complexity_profile(question, contract)
    request_spec = build_request_spec(question)
    proposed = Plan(
        [
            Subtask(
                "r1",
                "检索指南证据。",
                "research_agent",
                ["COMPREHENSIVE_CASE_ANALYSIS"],
                request_item_ids=["RQ1"],
            ),
            Subtask(
                "c1",
                "形成生活方式管理方案。",
                "consultation_agent",
                ["COMPREHENSIVE_CASE_ANALYSIS"],
                request_item_ids=["RQ1"],
            ),
        ]
    )

    constrained = apply_contract_policy(proposed, contract, profile, request_spec)
    route = Router().route(constrained, profile)

    assert profile.requires_external_evidence
    assert profile.requires_cross_role_reasoning
    assert route.mode == "multi"
    assert route.workers == ["research_agent", "consultation_agent"]


def test_same_worker_deliverables_are_merged_without_losing_contract_mapping() -> None:
    question = "Give the most likely diagnosis and further tests."
    contract = build_answer_contract(question)
    profile = build_complexity_profile(question, contract)
    proposed = Plan(
        [
            Subtask(
                "d1", "Identify the diagnosis.", "diagnostic_agent", ["DIAGNOSIS_WITH_BASIS"]
            ),
            Subtask(
                "d2", "List necessary tests.", "diagnostic_agent", ["FURTHER_TESTS"]
            ),
        ]
    )

    constrained = apply_contract_policy(proposed, contract, profile)

    assert len(constrained.subtasks) == 1
    assert set(constrained.subtasks[0].deliverable_ids) == set(
        contract.requested_deliverables
    )
    assert Router().route(constrained, profile).mode == "single"


def test_comprehensive_plan_is_not_forced_to_single() -> None:
    question = "Provide a comprehensive diagnosis, differential, tests, and treatment plan."
    contract = build_answer_contract(question)
    profile = build_complexity_profile(question, contract)
    proposed = Plan(
        [
            Subtask(
                "d1",
                "Diagnosis, differential, and tests.",
                "diagnostic_agent",
                [
                    "DIAGNOSIS_WITH_BASIS",
                    "DIFFERENTIAL_DIAGNOSIS",
                    "FURTHER_TESTS",
                ],
            ),
            Subtask(
                "m1",
                "Treatment plan.",
                "consultation_agent",
                ["TREATMENT_PLAN"],
            ),
        ]
    )

    constrained = apply_contract_policy(proposed, contract, profile)

    assert profile.requires_multi_agent
    assert len(constrained.subtasks) == 2
    assert Router().route(constrained, profile).mode == "multi"


@async_test
async def test_retrieval_schema_and_execution_are_gated_by_external_need() -> None:
    ordinary = "What is the most likely diagnosis?"
    ordinary_contract = build_answer_contract(ordinary)
    ordinary_profile = build_complexity_profile(ordinary, ordinary_contract)
    registry = ToolRegistry(2, tool_capabilities(ordinary_profile))
    registry.register("clinical_guideline", lambda query: query)
    registry.register("search_knowledge", lambda query: query)

    diagnostic_names = {
        item["function"]["name"]
        for item in registry.schemas_for("diagnostic_agent")
    }
    assert "clinical_guideline" not in diagnostic_names
    assert "search_knowledge" not in diagnostic_names
    assert registry.schemas_for("research_agent") == []
    with pytest.raises(PermissionError):
        await registry.execute("research_agent", "search_knowledge", {"query": "x"})

    guideline = "Please use the relevant clinical guideline for the treatment plan."
    guideline_contract = build_answer_contract(guideline)
    guideline_profile = build_complexity_profile(guideline, guideline_contract)
    guideline_registry = ToolRegistry(2, tool_capabilities(guideline_profile))
    guideline_registry.register("clinical_guideline", lambda query: query)
    names = {
        item["function"]["name"]
        for item in guideline_registry.schemas_for("consultation_agent")
    }
    assert guideline_profile.requires_external_evidence
    assert names == {"clinical_guideline"}
    assert await guideline_registry.execute(
        "consultation_agent", "clinical_guideline", {"query": "guideline"}
    )


def test_explicit_no_diagnosis_does_not_create_diagnostic_work() -> None:
    question = "请结合临床指南概述成人高血压治疗原则；仅总结证据，不作患者个体诊断。"
    contract = build_answer_contract(question)
    profile = build_complexity_profile(question, contract)

    assert "DIAGNOSIS_WITH_BASIS" not in contract.requested_deliverables
    assert contract.requested_deliverables == ["TREATMENT_PLAN"]
    assert not profile.requires_diagnosis
    assert profile.external_evidence_kind == "guideline"
    capabilities = tool_capabilities(profile)
    assert capabilities["research_agent"] == {"clinical_guideline"}


def test_response_profiles_control_scope_without_character_caps() -> None:
    focused_question = "What is the treatment?"
    focused_contract = build_answer_contract(focused_question)
    focused = build_response_profile(
        build_complexity_profile(focused_question, focused_contract)
    )
    comprehensive_question = (
        "Provide a comprehensive diagnosis, differential, tests, and treatment plan."
    )
    comprehensive_contract = build_answer_contract(comprehensive_question)
    comprehensive = build_response_profile(
        build_complexity_profile(comprehensive_question, comprehensive_contract)
    )

    assert focused.name == "focused"
    assert "only the requested deliverables" in focused.objective
    assert comprehensive.name == "comprehensive"
    assert "character" not in focused.objective.casefold()


def test_focused_safety_dependency_is_preserved_in_response_objective() -> None:
    question = "What is the immediate treatment?"
    contract = build_answer_contract(question)
    response = build_response_profile(build_complexity_profile(question, contract))

    assert response.name == "focused"
    assert "safety advice" in response.objective
