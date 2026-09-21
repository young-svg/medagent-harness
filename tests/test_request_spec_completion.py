from __future__ import annotations

import asyncio
from functools import wraps
from typing import Any

import pytest

from medagent.agents.base import RequestItemAnswer, WorkerResult
from medagent.context.contract import build_answer_contract
from medagent.context.request_spec import RequestItem, RequestSpec, build_request_spec
from medagent.llm import ScriptedLLM
from medagent.observability.replay import read_trace
from medagent.planning.complexity import build_complexity_profile
from medagent.planning.models import Plan, Subtask
from medagent.planning.planner import apply_contract_policy
from medagent.runtime.config import RuntimeConfig
from medagent.runtime.coverage import evaluate_request_coverage
from medagent.runtime.native_engine import NativeMedAgentEngine


def async_test(function: Any) -> Any:
    @wraps(function)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return asyncio.run(function(*args, **kwargs))

    return wrapper


@pytest.mark.parametrize(
    "question",
    [
        "请回答：\n1. 该病表现是什么？\n2. 为什么会发生？\n3. 如何治疗？",
        "请回答：\n1）该病表现是什么？\n2）为什么会发生？\n3）如何治疗？",
        "请回答：\n（1）该病表现是什么？\n（2）为什么会发生？\n（3）如何治疗？",
        "请回答：\n① 该病表现是什么？\n② 为什么会发生？\n③ 如何治疗？",
        "请回答：\n- 该病表现是什么？\n- 为什么会发生？\n- 如何治疗？",
    ],
)
def test_t1_explicit_three_requests_are_preserved(question: str) -> None:
    spec = build_request_spec(question)

    assert [item.id for item in spec.items] == ["RQ1", "RQ2", "RQ3"]
    assert all(item.required for item in spec.items)
    assert [item.semantic_type for item in spec.items] == [
        "UNKNOWN",
        "UNKNOWN",
        "TREATMENT_PLAN",
    ]
    assert all(item.source_span in question for item in spec.items)


def test_t2_unknown_item_remains_required() -> None:
    question = "1. 该治疗最大的益处是什么？\n2. 术前需要做什么？"
    spec = build_request_spec(question)

    assert len(spec.items) == 2
    assert spec.items[0].semantic_type == "UNKNOWN"
    assert spec.items[0].required is True
    assert spec.items[1].semantic_type == "PREOPERATIVE_EVALUATION"
    assert build_answer_contract(question).requested_deliverables == [
        "PREOPERATIVE_EVALUATION"
    ]


def test_t3_three_same_role_items_can_merge_into_one_worker() -> None:
    question = "1. 如何治疗？\n2. 术前如何处理？\n3. 术后如何管理？"
    spec = build_request_spec(question)
    contract = build_answer_contract(question)
    complexity = build_complexity_profile(question, contract)
    proposed = Plan(
        [
            Subtask("one", "Treatment.", "consultation_agent", ["TREATMENT_PLAN"]),
            Subtask(
                "two",
                "Perioperative care.",
                "consultation_agent",
                ["PREOPERATIVE_EVALUATION", "POSTOPERATIVE_MANAGEMENT"],
            ),
        ]
    )

    plan = apply_contract_policy(proposed, contract, complexity, spec)

    assert len(plan.subtasks) == 1
    assert plan.subtasks[0].request_item_ids == ["RQ1", "RQ2", "RQ3"]


def test_t4_t5_worker_partial_and_complete_coverage() -> None:
    spec = build_request_spec("1. 问题一？\n2. 问题二？\n3. 问题三？")
    partial = WorkerResult(
        "diagnostic_agent",
        "task",
        "one\n\ntwo",
        request_item_answers=[
            RequestItemAnswer("RQ1", "one"),
            RequestItemAnswer("RQ2", "two"),
        ],
    )
    partial_coverage = evaluate_request_coverage(spec, [partial])
    complete = WorkerResult(
        "diagnostic_agent",
        "task",
        "all",
        request_item_answers=partial.request_item_answers
        + [RequestItemAnswer("RQ3", "three")],
    )

    assert partial_coverage.missing_request_items == ["RQ3"]
    assert partial_coverage.complete is False
    assert evaluate_request_coverage(spec, [complete]).complete is True


def test_t6_duplicate_coverage_uses_union() -> None:
    spec = build_request_spec("1. 问题一？\n2. 问题二？")
    workers = [
        WorkerResult(
            "diagnostic_agent",
            "one",
            "a",
            request_item_answers=[RequestItemAnswer("RQ1", "a")],
        ),
        WorkerResult(
            "consultation_agent",
            "two",
            "b",
            request_item_answers=[
                RequestItemAnswer("RQ1", "duplicate"),
                RequestItemAnswer("RQ2", "b"),
            ],
        ),
    ]

    coverage = evaluate_request_coverage(spec, workers)

    assert coverage.covered_request_items == ["RQ1", "RQ2"]
    assert coverage.request_item_answers == {"RQ1": "a", "RQ2": "b"}


def test_t7_optional_item_does_not_block_completion() -> None:
    spec = RequestSpec(
        [
            RequestItem("RQ1", "required", True, 1, "required", "UNKNOWN"),
            RequestItem("RQ2", "optional", False, 2, "optional", "UNKNOWN"),
        ]
    )
    worker = WorkerResult(
        "diagnostic_agent",
        "one",
        "done",
        request_item_answers=[RequestItemAnswer("RQ1", "done")],
    )

    coverage = evaluate_request_coverage(spec, [worker])

    assert coverage.complete is True
    assert coverage.covered_request_items == ["RQ1"]


def test_t8_unstructured_question_is_one_lossless_item() -> None:
    question = "这种情况下应如何平衡获益、风险以及患者偏好？"
    spec = build_request_spec(question)

    assert len(spec.items) == 1
    assert spec.items[0].text == question
    assert spec.items[0].source_span == question


def test_t9_existing_tests_and_further_tests_both_remain_request_items() -> None:
    question = "1. 分析已有辅助检查。\n2. 下一步还需要做哪些检查？"
    spec = build_request_spec(question)

    assert len(spec.items) == 2
    assert spec.items[0].semantic_type == "UNKNOWN"
    assert spec.items[1].semantic_type == "FURTHER_TESTS"


def _answer(*pairs: tuple[str, str]) -> dict[str, object]:
    return {
        "answers": [
            {"request_item_id": request_item_id, "answer": answer}
            for request_item_id, answer in pairs
        ]
    }


@async_test
async def test_smoke_a_three_same_role_requests_stay_single(tmp_path) -> None:
    question = "1. 临床表现是什么？\n2. 病因是什么？\n3. 如何治疗？"
    llm = ScriptedLLM(
        [
            {
                "subtasks": [
                    {
                        "subtask_id": "one",
                        "description": "Answer all consultation requests.",
                        "assigned_agent": "consultation_agent",
                        "deliverable_ids": ["TREATMENT_PLAN"],
                        "request_item_ids": ["RQ1", "RQ2", "RQ3"],
                    }
                ]
            },
            _answer(("RQ1", "manifestations"), ("RQ2", "cause"), ("RQ3", "care")),
        ]
    )
    engine = NativeMedAgentEngine(RuntimeConfig(trace_dir=str(tmp_path)), llm=llm)

    result = await engine.analyze("Synthetic case", question, "same-role")
    await engine.close()

    assert result["status"] == "completed"
    assert result["user_request_complete"] is True
    assert result["presentation"]["execution_summary"]["route"]["mode"] == "single"


@async_test
async def test_smoke_b_three_cross_role_requests_use_multi(tmp_path) -> None:
    question = "1. 诊断是什么？\n2. 下一步需要哪些检查？\n3. 如何治疗？"
    llm = ScriptedLLM(
        [
            {
                "subtasks": [
                    {
                        "subtask_id": "diagnosis",
                        "description": "Diagnosis and tests.",
                        "assigned_agent": "diagnostic_agent",
                        "deliverable_ids": ["DIAGNOSIS_WITH_BASIS", "FURTHER_TESTS"],
                        "request_item_ids": ["RQ1", "RQ2"],
                    },
                    {
                        "subtask_id": "care",
                        "description": "Treatment.",
                        "assigned_agent": "consultation_agent",
                        "deliverable_ids": ["TREATMENT_PLAN"],
                        "request_item_ids": ["RQ3"],
                    },
                ]
            },
            _answer(("RQ1", "diagnosis"), ("RQ2", "tests")),
            _answer(("RQ3", "treatment")),
            "Combined answer covering diagnosis, tests, and treatment.",
        ]
    )
    engine = NativeMedAgentEngine(RuntimeConfig(trace_dir=str(tmp_path)), llm=llm)

    result = await engine.analyze("Synthetic case", question, "cross-role")
    await engine.close()

    assert result["status"] == "completed"
    assert result["presentation"]["execution_summary"]["route"]["mode"] == "multi"
    assert set(result["request_item_answers"]) == {"RQ1", "RQ2", "RQ3"}


@async_test
async def test_smoke_c_worker_omission_is_incomplete(tmp_path) -> None:
    question = "1. 临床表现是什么？\n2. 病因是什么？\n3. 如何治疗？"
    llm = ScriptedLLM(
        [
            {
                "subtasks": [
                    {
                        "subtask_id": "one",
                        "description": "Answer all requests.",
                        "assigned_agent": "consultation_agent",
                        "deliverable_ids": ["TREATMENT_PLAN"],
                        "request_item_ids": ["RQ1", "RQ2", "RQ3"],
                    }
                ]
            },
            _answer(("RQ1", "manifestations"), ("RQ2", "cause")),
        ]
    )
    engine = NativeMedAgentEngine(RuntimeConfig(trace_dir=str(tmp_path)), llm=llm)

    result = await engine.analyze("Synthetic case", question, "omission")
    await engine.close()
    events = read_trace(tmp_path / result["run_id"])

    assert result["status"] == "incomplete"
    assert result["user_request_complete"] is False
    assert result["contract_complete"] is True
    assert result["missing_required_request_items"] == ["RQ3"]
    coverage = next(event for event in events if event["event_type"] == "request_coverage")
    assert coverage["payload"]["covered_request_items"] == ["RQ1", "RQ2"]
    assert coverage["payload"]["missing_request_items"] == ["RQ3"]


@async_test
async def test_smoke_d_unknown_taxonomy_can_complete(tmp_path) -> None:
    question = "1. 该治疗最大的益处是什么？\n2. 术前需要做什么？"
    llm = ScriptedLLM(
        [
            {
                "subtasks": [
                    {
                        "subtask_id": "one",
                        "description": "Answer both requests.",
                        "assigned_agent": "consultation_agent",
                        "deliverable_ids": ["PREOPERATIVE_EVALUATION"],
                        "request_item_ids": ["RQ1", "RQ2"],
                    }
                ]
            },
            _answer(("RQ1", "benefit"), ("RQ2", "preoperative steps")),
        ]
    )
    engine = NativeMedAgentEngine(RuntimeConfig(trace_dir=str(tmp_path)), llm=llm)

    result = await engine.analyze("Synthetic case", question, "unknown")
    await engine.close()

    assert result["status"] == "completed"
    assert result["user_request_complete"] is True
    request_spec = result["presentation"]["execution_summary"]["request_spec"]
    assert request_spec["items"][0]["semantic_type"] == "UNKNOWN"
