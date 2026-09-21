from __future__ import annotations

import asyncio
import json
from functools import wraps
from typing import Any

from medagent.context.contract import build_answer_contract
from medagent.context.evidence_ledger import build_evidence_ledger
from medagent.llm import LLMResponse, ScriptedLLM
from medagent.observability.replay import read_trace
from medagent.observability.tracer import TraceRecorder
from medagent.planning.planner import Planner
from medagent.runtime.config import RuntimeConfig
from medagent.runtime.native_engine import NativeMedAgentEngine


def async_test(function: Any) -> Any:
    @wraps(function)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return asyncio.run(function(*args, **kwargs))

    return wrapper


def valid_plan() -> str:
    return json.dumps(
        {
            "subtasks": [
                {
                    "subtask_id": "task-1",
                    "description": "Assess the requested deliverable.",
                    "assigned_agent": "diagnostic_agent",
                }
            ]
        }
    )


def length_response(content: str = "") -> LLMResponse:
    return LLMResponse(
        content,
        usage={
            "completion_tokens": 1200,
            "total_tokens": 1200,
            "completion_tokens_details": {"reasoning_tokens": 1200},
        },
        finish_reason="length",
    )


async def run_plan(llm: ScriptedLLM, trace: TraceRecorder | None = None):
    return await Planner(llm, max_tokens=8192, max_length_recoveries=1).plan(
        "What tests are needed?",
        build_answer_contract("What tests are needed?"),
        build_evidence_ledger("Synthetic fatigue"),
        trace,
    )


@async_test
async def test_planner_recovers_once_after_empty_length_response(tmp_path) -> None:
    llm = ScriptedLLM([length_response(), LLMResponse(valid_plan(), finish_reason="stop")])
    trace = TraceRecorder(tmp_path)

    plan = await run_plan(llm, trace)

    assert len(llm.requests) == 2
    assert plan.fallback_reason is None
    assert plan.planner_parse_status == "direct_json"
    assert plan.planner_parse_failure is False
    assert plan.planner_generation_status == "completed_after_length_recovery"
    events = read_trace(tmp_path / trace.run_id)
    planner_requests = [event for event in events if event["event_type"] == "llm_request"]
    assert [event["payload"]["attempt_index"] for event in planner_requests] == [1, 2]
    assert planner_requests[1]["payload"]["recovery_type"] == (
        "structured_output_completion_recovery"
    )


@async_test
async def test_planner_stops_after_one_failed_length_recovery(tmp_path) -> None:
    llm = ScriptedLLM([length_response(), length_response()])
    trace = TraceRecorder(tmp_path)

    plan = await run_plan(llm, trace)

    assert len(llm.requests) == 2
    assert plan.planner_generation_status == "length_exhausted"
    assert plan.planner_parse_status == "fallback"
    assert plan.planner_parse_failure is True
    assert plan.fallback_reason == "structured_parse_fallback:JSONDecodeError"
    events = read_trace(tmp_path / trace.run_id)
    assert len([event for event in events if event["event_type"] == "llm_request"]) == 2
    assert len([event for event in events if event["event_type"] == "llm_response"]) == 2


@async_test
async def test_planner_completes_on_first_attempt() -> None:
    llm = ScriptedLLM([LLMResponse(valid_plan(), finish_reason="stop")])

    plan = await run_plan(llm)

    assert len(llm.requests) == 1
    assert plan.planner_generation_status == "completed_first_attempt"
    assert plan.planner_parse_status == "direct_json"
    assert plan.planner_parse_failure is False


@async_test
async def test_planner_budget_is_independent_from_worker_and_synthesis_budget(tmp_path) -> None:
    config = RuntimeConfig()
    assert config.planner_max_tokens == 8192
    assert config.planner_max_length_recoveries == 1
    assert config.llm_max_tokens == 1200

    llm = ScriptedLLM(
        [
            {
                "subtasks": [
                    {
                        "subtask_id": "task-1",
                        "description": "Assess the request.",
                        "assigned_agent": "diagnostic_agent",
                    },
                    {
                        "subtask_id": "task-2",
                        "description": "Review the request.",
                        "assigned_agent": "consultation_agent",
                    },
                ]
            },
            "Diagnostic draft.",
            "Consultation draft.",
            "Combined synthesis.",
        ]
    )
    engine = NativeMedAgentEngine(
        RuntimeConfig(trace_dir=str(tmp_path)),
        llm=llm,
    )
    await engine.analyze("Synthetic case", "Assess safely", "budget-test")
    await engine.close()

    assert llm.requests[0]["max_tokens"] == 8192
    assert all(request["max_tokens"] == 1200 for request in llm.requests[1:])


@async_test
async def test_complete_json_at_length_does_not_trigger_recovery() -> None:
    llm = ScriptedLLM([length_response(valid_plan())])

    plan = await run_plan(llm)

    assert len(llm.requests) == 1
    assert plan.fallback_reason is None
    assert plan.planner_generation_status == "completed_first_attempt"
    assert plan.planner_parse_status == "direct_json"
