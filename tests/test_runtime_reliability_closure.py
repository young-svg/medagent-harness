from __future__ import annotations

import asyncio
from collections import deque
from functools import wraps
from typing import Any

import anyio
import httpx
import pytest

from medagent.context.contract import AnswerContract
from medagent.context.models import CoverageItem
from medagent.llm import LLMResponse, ToolCall
from medagent.observability.replay import read_trace
from medagent.runtime.config import RuntimeConfig
from medagent.runtime.native_engine import NativeMedAgentEngine


def async_test(function: Any) -> Any:
    @wraps(function)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return asyncio.run(function(*args, **kwargs))

    return wrapper


class FailureInjectingLLM:
    model = "failure-injection-model"
    temperature = 0.0
    max_tokens = 1200

    def __init__(self, responses: list[object]) -> None:
        self.responses = deque(responses)
        self.requests: list[dict[str, Any]] = []

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        self.requests.append(
            {
                "messages": messages,
                "tools": tools or [],
                "response_format": response_format,
                "max_tokens": max_tokens,
            }
        )
        response = self.responses.popleft()
        if isinstance(response, BaseException):
            raise response
        if isinstance(response, dict):
            return LLMResponse(
                __import__("json").dumps(response),
                finish_reason="stop",
                model=self.model,
            )
        if isinstance(response, LLMResponse):
            response.model = response.model or self.model
            response.finish_reason = response.finish_reason or (
                "tool_calls" if response.tool_calls else "stop"
            )
            return response
        return LLMResponse(str(response), finish_reason="stop", model=self.model)

    async def close(self) -> None:
        return None


def two_deliverable_plan(
    first: list[str] | None = None, second: list[str] | None = None
) -> dict[str, object]:
    return {
        "subtasks": [
            {
                "subtask_id": "diagnosis-task",
                "description": "Complete diagnosis deliverable.",
                "assigned_agent": "diagnostic_agent",
                "deliverable_ids": first or ["DIAGNOSIS_WITH_BASIS"],
            },
            {
                "subtask_id": "treatment-task",
                "description": "Complete treatment deliverable.",
                "assigned_agent": "consultation_agent",
                "deliverable_ids": second or ["TREATMENT_PLAN"],
            },
        ]
    }


def contract(
    *, must_cover: list[str] | None = None, optional_cover: list[str] | None = None
) -> AnswerContract:
    required = must_cover or ["diagnosis with basis", "treatment plan"]
    optional = optional_cover or []
    return AnswerContract(
        intent="multi_deliverable",
        requested_deliverables=["DIAGNOSIS_WITH_BASIS", "TREATMENT_PLAN"],
        must_cover=required,
        optional_cover=optional,
        coverage_checklist=[CoverageItem(item=item) for item in required]
        + [CoverageItem(item=item, priority="OPTIONAL") for item in optional],
    )


def patch_contract(monkeypatch: pytest.MonkeyPatch, value: AnswerContract) -> None:
    monkeypatch.setattr(
        "medagent.runtime.native_engine.build_answer_contract", lambda _question: value
    )


def worker_requests(llm: FailureInjectingLLM) -> list[dict[str, Any]]:
    return [request for request in llm.requests if request["response_format"] is None]


@async_test
async def test_worker_infrastructure_retry_recovers_and_completes(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_contract(monkeypatch, contract())
    llm = FailureInjectingLLM(
        [
            two_deliverable_plan(),
            "Diagnosis draft",
            httpx.ConnectError("temporary connect failure"),
            "Treatment draft",
            "Synthesis output",
        ]
    )
    engine = NativeMedAgentEngine(RuntimeConfig(trace_dir=str(tmp_path)), llm=llm)

    result = await engine.analyze("Synthetic case", "diagnosis and treatment", "retry-ok")
    await engine.close()
    workers = result["presentation"]["execution_summary"]["workers"]

    assert len(worker_requests(llm)) == 4
    assert workers[1]["provider_attempt_count"] == 2
    assert workers[1]["infrastructure_retry_count"] == 1
    assert result["status"] == "completed"
    assert result["missing_required_deliverables"] == []
    assert result["final_answer"]
    assert result["trace"]["worker_infrastructure_retry_count"] == 1
    assert result["trace"]["workers_recovered_after_infra_retry"] == 1
    events = read_trace(tmp_path / result["run_id"])
    attempts = [
        event["payload"]
        for event in events
        if event["event_type"] == "worker_provider_attempt"
        and event["payload"]["subtask_id"] == "treatment-task"
    ]
    assert [attempt["attempt_index"] for attempt in attempts] == [1, 2]
    assert [attempt["retry_type"] for attempt in attempts] == [None, "infrastructure_retry"]
    assert attempts[0]["error_type"] == "ConnectError"
    assert attempts[0]["error_message_sanitized"] == "temporary connect failure"
    assert attempts[1]["outcome"] == "success"
    assert all(attempt["provider"] == "FailureInjectingLLM" for attempt in attempts)
    assert all(attempt["model"] == "failure-injection-model" for attempt in attempts)
    assert all(isinstance(attempt["latency_ms"], float) for attempt in attempts)


@async_test
async def test_worker_infrastructure_retry_exhaustion_is_incomplete(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_contract(monkeypatch, contract())
    llm = FailureInjectingLLM(
        [
            two_deliverable_plan(),
            "Diagnosis draft",
            httpx.ConnectError("first temporary failure"),
            httpx.ConnectError("second temporary failure"),
        ]
    )
    engine = NativeMedAgentEngine(RuntimeConfig(trace_dir=str(tmp_path)), llm=llm)

    result = await engine.analyze("Synthetic case", "diagnosis and treatment", "retry-fail")
    await engine.close()
    workers = result["presentation"]["execution_summary"]["workers"]
    events = read_trace(tmp_path / result["run_id"])

    assert workers[1]["worker_status"] == "provider_error"
    assert workers[1]["provider_attempt_count"] == 2
    assert result["status"] != "completed"
    assert result["missing_required_deliverables"] == ["TREATMENT_PLAN"]
    assert result["successful_workers"] == 1
    assert result["failed_workers"] == 1
    assert not any(event["event_type"].startswith("synthesis_") for event in events)
    assert events[-1]["payload"]["status"] == "incomplete"
    assert result["trace"]["workers_failed_after_infra_retry"] == 1


@async_test
async def test_duplicate_required_coverage_allows_completion(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_contract(
        monkeypatch,
        contract(must_cover=["diagnosis with basis"], optional_cover=["treatment plan"]),
    )
    llm = FailureInjectingLLM(
        [
            two_deliverable_plan(
                ["DIAGNOSIS_WITH_BASIS"], ["DIAGNOSIS_WITH_BASIS"]
            ),
            httpx.ConnectError("failure one"),
            httpx.ConnectError("failure two"),
            "Duplicate coverage succeeds",
        ]
    )
    engine = NativeMedAgentEngine(RuntimeConfig(trace_dir=str(tmp_path)), llm=llm)

    result = await engine.analyze("Synthetic case", "diagnosis and treatment", "duplicate")
    await engine.close()

    assert result["status"] == "completed"
    assert result["missing_required_deliverables"] == []


@async_test
async def test_optional_deliverable_failure_does_not_block_completion(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_contract(
        monkeypatch,
        contract(must_cover=["diagnosis with basis"], optional_cover=["treatment plan"]),
    )
    llm = FailureInjectingLLM(
        [
            two_deliverable_plan(),
            "Required diagnosis draft",
            httpx.ConnectError("failure one"),
            httpx.ConnectError("failure two"),
        ]
    )
    engine = NativeMedAgentEngine(RuntimeConfig(trace_dir=str(tmp_path)), llm=llm)

    result = await engine.analyze("Synthetic case", "diagnosis and treatment", "optional")
    await engine.close()

    assert result["status"] == "completed"
    assert result["missing_required_deliverables"] == []


@async_test
async def test_extra_worker_text_cannot_claim_unassigned_coverage(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_contract(monkeypatch, contract())
    llm = FailureInjectingLLM(
        [
            two_deliverable_plan(),
            "Diagnosis draft that also literally says TREATMENT_PLAN",
            httpx.ConnectError("failure one"),
            httpx.ConnectError("failure two"),
        ]
    )
    engine = NativeMedAgentEngine(RuntimeConfig(trace_dir=str(tmp_path)), llm=llm)

    result = await engine.analyze("Synthetic case", "diagnosis and treatment", "extra-text")
    await engine.close()

    assert result["status"] == "incomplete"
    assert result["missing_required_deliverables"] == ["TREATMENT_PLAN"]


@async_test
async def test_single_complete_still_bypasses_synthesis(tmp_path) -> None:
    llm = FailureInjectingLLM(
        [
            {
                "subtasks": [
                    {
                        "subtask_id": "only",
                        "description": "Complete assessment.",
                        "assigned_agent": "diagnostic_agent",
                    }
                ]
            },
            "Single complete answer",
        ]
    )
    engine = NativeMedAgentEngine(RuntimeConfig(trace_dir=str(tmp_path)), llm=llm)

    result = await engine.analyze("Synthetic case", "Assess safely", "single-complete")
    await engine.close()
    events = read_trace(tmp_path / result["run_id"])

    assert result["status"] == "completed"
    assert not any(event["event_type"].startswith("synthesis_") for event in events)


@async_test
async def test_multi_complete_still_synthesizes(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_contract(monkeypatch, contract())
    llm = FailureInjectingLLM(
        [two_deliverable_plan(), "Diagnosis", "Treatment", "Combined answer"]
    )
    engine = NativeMedAgentEngine(RuntimeConfig(trace_dir=str(tmp_path)), llm=llm)

    result = await engine.analyze("Synthetic case", "diagnosis and treatment", "multi-complete")
    await engine.close()
    events = read_trace(tmp_path / result["run_id"])

    assert result["status"] == "completed"
    assert any(event["event_type"] == "synthesis_output" for event in events)


@async_test
async def test_retry_after_tool_does_not_repeat_tool_side_effect(tmp_path) -> None:
    llm = FailureInjectingLLM(
        [
            {
                "subtasks": [
                    {
                        "subtask_id": "tool-worker",
                        "description": "Use the risk tool.",
                        "assigned_agent": "diagnostic_agent",
                    }
                ]
            },
            LLMResponse(
                tool_calls=[ToolCall("risk-1", "assess_risk", {"symptoms": "x"})]
            ),
            httpx.ConnectError("post-tool transient failure"),
            "Tool-aware answer",
        ]
    )
    engine = NativeMedAgentEngine(RuntimeConfig(trace_dir=str(tmp_path)), llm=llm)

    result = await engine.analyze("Synthetic case", "Assess safely", "tool-safe")
    await engine.close()
    events = read_trace(tmp_path / result["run_id"])

    assert result["status"] == "completed"
    assert sum(event["event_type"] == "tool_call" for event in events) == 1
    assert sum(event["event_type"] == "tool_result" for event in events) == 1


def status_error(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://provider.invalid/chat/completions")
    response = httpx.Response(code, request=request)
    return httpx.HTTPStatusError("provider status", request=request, response=response)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (httpx.ConnectError("connect"), True),
        (httpx.ReadTimeout("timeout"), True),
        (anyio.EndOfStream(), True),
        (TimeoutError("timeout"), True),
        (ConnectionResetError("reset"), True),
        (ConnectionAbortedError("aborted"), True),
        (status_error(429), True),
        (status_error(500), True),
        (status_error(503), True),
        (status_error(400), False),
        (ValueError("poor content quality"), False),
    ],
)
def test_infrastructure_retry_classification(error: BaseException, expected: bool) -> None:
    from medagent.llm.generation import is_retryable_infrastructure_error

    assert is_retryable_infrastructure_error(error) is expected


def test_worker_retry_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEDAGENT_WORKER_MAX_INFRA_RETRIES", "1")
    assert RuntimeConfig.from_env().worker_max_infrastructure_retries == 1
