from __future__ import annotations

import asyncio
from functools import wraps
from pathlib import Path
from typing import Any

import pytest

from medagent.llm import LLMResponse, ScriptedLLM, ToolCall
from medagent.observability.replay import read_trace
from medagent.runtime.config import RuntimeConfig
from medagent.runtime.engine import EngineExecutionError
from medagent.runtime.native_engine import NativeMedAgentEngine


def async_test(function: Any) -> Any:
    @wraps(function)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return asyncio.run(function(*args, **kwargs))

    return wrapper


def plan(*workers: str) -> dict[str, object]:
    return {
        "subtasks": [
            {
                "subtask_id": f"task-{index}",
                "description": f"Complete synthetic task {index}.",
                "assigned_agent": worker,
            }
            for index, worker in enumerate(workers, 1)
        ]
    }


def length_response(content: str = "") -> LLMResponse:
    return LLMResponse(
        content,
        usage={
            "completion_tokens": 8192,
            "total_tokens": 8192,
            "completion_tokens_details": {"reasoning_tokens": 8192},
        },
        finish_reason="length",
    )


def only_trace(root: Path) -> list[dict[str, Any]]:
    run_dirs = [path for path in root.iterdir() if path.is_dir()]
    assert len(run_dirs) == 1
    return read_trace(run_dirs[0])


def generation_summaries(
    events: list[dict[str, Any]], stage: str
) -> list[dict[str, Any]]:
    return [
        event
        for event in events
        if event["event_type"] == "generation_summary" and event["stage"] == stage
    ]


@async_test
async def test_worker_recovers_once_after_empty_length_response(tmp_path) -> None:
    llm = ScriptedLLM(
        [
            plan("diagnostic_agent"),
            length_response(),
            LLMResponse("VALID_WORKER_DRAFT", finish_reason="stop"),
        ]
    )
    engine = NativeMedAgentEngine(RuntimeConfig(trace_dir=str(tmp_path)), llm=llm)

    result = await engine.analyze("Synthetic case", "Assess safely", "worker-recovery")
    await engine.close()
    events = read_trace(tmp_path / result["run_id"])
    worker = result["presentation"]["execution_summary"]["workers"][0]

    assert len(llm.requests) == 3
    assert result["final_answer"] == "VALID_WORKER_DRAFT"
    assert worker["generation_status"] == "completed_after_length_recovery"
    assert worker["length_recovery_count"] == 1
    requests = [
        event
        for event in events
        if event["event_type"] == "llm_request" and event["stage"] == "worker"
    ]
    responses = [
        event
        for event in events
        if event["event_type"] == "llm_response" and event["stage"] == "worker"
    ]
    assert [event["payload"]["attempt_index"] for event in requests] == [1, 2]
    assert requests[1]["payload"]["recovery_type"] == "length_completion_recovery"
    assert all(event["payload"]["max_tokens"] == 8192 for event in requests + responses)
    assert [event["payload"]["finish_reason"] for event in responses] == ["length", "stop"]
    assert all(event["payload"]["tool_call_count"] == 0 for event in responses)
    assert responses[0]["payload"]["usage"]["reasoning_tokens"] == 8192
    assert all(isinstance(event["payload"]["latency_ms"], float) for event in responses)
    assert all(event["payload"]["error"] is None for event in responses)
    assert generation_summaries(events, "worker")[-1]["payload"] == {
        "purpose": "worker:task-1",
        "generation_status": "completed_after_length_recovery",
        "length_recovery_count": 1,
        "attempt_count": 2,
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 8192,
            "reasoning_tokens": 8192,
            "total_tokens": 8192,
        },
        "latency_ms": pytest.approx(0, abs=20),
    }


@async_test
async def test_worker_length_exhaustion_fails_without_fake_draft(tmp_path) -> None:
    llm = ScriptedLLM(
        [plan("diagnostic_agent"), length_response(), length_response()]
    )
    engine = NativeMedAgentEngine(RuntimeConfig(trace_dir=str(tmp_path)), llm=llm)

    with pytest.raises(EngineExecutionError, match="all workers failed"):
        await engine.analyze("Synthetic case", "Assess safely", "worker-exhausted")
    await engine.close()
    events = only_trace(tmp_path)
    draft = next(event for event in events if event["event_type"] == "worker_draft")

    assert len(llm.requests) == 3
    assert draft["payload"]["answer"] == ""
    assert draft["payload"]["success"] is False
    assert draft["payload"]["generation_status"] == "length_exhausted"
    assert draft["payload"]["failure_reason"] == "generation_length_exhausted"
    assert events[-1]["event_type"] == "run_end"
    assert events[-1]["payload"]["status"] == "failed"


@async_test
async def test_worker_tool_call_is_usable_without_length_recovery(tmp_path) -> None:
    llm = ScriptedLLM(
        [
            plan("diagnostic_agent"),
            LLMResponse(
                "",
                tool_calls=[ToolCall("call-1", "assess_risk", {"symptoms": "synthetic"})],
                finish_reason="length",
            ),
            LLMResponse("VALID_TOOL_AWARE_DRAFT", finish_reason="stop"),
        ]
    )
    engine = NativeMedAgentEngine(RuntimeConfig(trace_dir=str(tmp_path)), llm=llm)

    result = await engine.analyze("Synthetic case", "Assess safely", "tool-call")
    await engine.close()
    events = read_trace(tmp_path / result["run_id"])
    summaries = generation_summaries(events, "worker")

    assert len(llm.requests) == 3
    assert result["final_answer"] == "VALID_TOOL_AWARE_DRAFT"
    assert summaries[0]["payload"]["generation_status"] == "completed_first_attempt"
    assert summaries[0]["payload"]["length_recovery_count"] == 0
    assert any(event["event_type"] == "tool_call" for event in events)


@async_test
async def test_synthesis_recovers_once_after_empty_length_response(tmp_path) -> None:
    llm = ScriptedLLM(
        [
            plan("diagnostic_agent", "research_agent"),
            "Diagnostic draft",
            "Research draft",
            length_response(),
            LLMResponse("VALID_SYNTHESIZED_ANSWER", finish_reason="stop"),
        ]
    )
    engine = NativeMedAgentEngine(RuntimeConfig(trace_dir=str(tmp_path)), llm=llm)

    result = await engine.analyze("Synthetic case", "Assess and research", "synth-recovery")
    await engine.close()
    events = read_trace(tmp_path / result["run_id"])
    output = next(event for event in events if event["event_type"] == "synthesis_output")

    assert len(llm.requests) == 5
    assert result["final_answer"] == "VALID_SYNTHESIZED_ANSWER"
    assert output["payload"]["generation_status"] == "completed_after_length_recovery"
    assert output["payload"]["length_recovery_count"] == 1


@async_test
async def test_synthesis_length_exhaustion_marks_run_failed(tmp_path) -> None:
    llm = ScriptedLLM(
        [
            plan("diagnostic_agent", "research_agent"),
            "Diagnostic draft",
            "Research draft",
            length_response(),
            length_response(),
        ]
    )
    engine = NativeMedAgentEngine(RuntimeConfig(trace_dir=str(tmp_path)), llm=llm)

    with pytest.raises(EngineExecutionError, match="synthesis generation failed"):
        await engine.analyze("Synthetic case", "Assess and research", "synth-exhausted")
    await engine.close()
    events = only_trace(tmp_path)
    output = next(event for event in events if event["event_type"] == "synthesis_output")
    error = next(
        event
        for event in events
        if event["event_type"] == "error" and event["stage"] == "lifecycle"
    )

    assert len(llm.requests) == 5
    assert output["payload"]["draft"] == ""
    assert output["payload"]["generation_status"] == "length_exhausted"
    assert output["payload"]["length_recovery_count"] == 1
    assert error["payload"]["failure_stage"] == "synthesis"
    assert error["payload"]["failure_reason"] == "generation_length_exhausted"
    assert not any(event["event_type"] == "final_answer" for event in events)
    assert events[-1]["payload"]["status"] == "failed"


@async_test
async def test_stage_generation_budgets_are_independent(tmp_path) -> None:
    config = RuntimeConfig(trace_dir=str(tmp_path))
    assert config.llm_max_tokens == 1200
    assert config.planner_max_tokens == 8192
    assert config.worker_max_tokens == 8192
    assert config.synthesis_max_tokens == 8192

    llm = ScriptedLLM(
        [
            plan("diagnostic_agent", "consultation_agent"),
            "Diagnostic draft",
            "Consultation draft",
            "Synthesis draft",
        ]
    )
    engine = NativeMedAgentEngine(config, llm=llm)

    await engine.analyze(
        "Synthetic case",
        "Assess the diagnosis and treatment safely.",
        "budget-isolation",
    )
    await engine.close()

    assert [request["max_tokens"] for request in llm.requests] == [8192, 8192, 8192, 8192]


@async_test
async def test_normal_worker_and_synthesis_do_not_add_calls(tmp_path) -> None:
    llm = ScriptedLLM(
        [
            plan("diagnostic_agent", "research_agent"),
            "Diagnostic draft",
            "Research draft",
            "Synthesis draft",
        ]
    )
    engine = NativeMedAgentEngine(RuntimeConfig(trace_dir=str(tmp_path)), llm=llm)

    result = await engine.analyze("Synthetic case", "Assess and research", "no-recovery")
    await engine.close()
    events = read_trace(tmp_path / result["run_id"])
    summaries = generation_summaries(events, "worker") + generation_summaries(
        events, "synthesis"
    )

    assert len(llm.requests) == 4
    assert len(summaries) == 3
    assert all(
        event["payload"]["generation_status"] == "completed_first_attempt"
        for event in summaries
    )
    assert all(event["payload"]["length_recovery_count"] == 0 for event in summaries)
