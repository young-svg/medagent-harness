from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Any

import httpx
import pytest

from medagent.agents.base import AgentDefinition
from medagent.context.contract import AnswerContract
from medagent.context.evidence_ledger import EvidenceLedger
from medagent.context.models import CoverageItem
from medagent.context.request_spec import RequestItem, RequestSpec
from medagent.llm.client import LLMResponse, ToolCall
from medagent.observability.replay import read_trace
from medagent.observability.tracer import TraceRecorder
from medagent.planning.complexity import ResponseProfile, TaskComplexityProfile
from medagent.planning.models import Subtask
from medagent.runtime.agent_loop import AgentLoop
from medagent.runtime.coverage import (
    evaluate_contract_coverage,
    evaluate_request_coverage,
)
from medagent.skills.loader import ProceduralSkill
from medagent.tools.registry import ToolRegistry


class QueueLLM:
    model = "protocol-recovery-test-model"
    temperature = 0.0
    max_tokens = 8192

    def __init__(self, responses: list[LLMResponse | BaseException]) -> None:
        self.responses = deque(responses)
        self.requests: list[dict[str, Any]] = []

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        self.requests.append(
            {
                "messages": messages,
                "tools": tools or [],
                "response_format": response_format,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        )
        value = self.responses.popleft()
        if isinstance(value, BaseException):
            raise value
        value.model = value.model or self.model
        return value

    async def close(self) -> None:
        return None


def response_json(*pairs: tuple[str, str]) -> str:
    return json.dumps(
        {
            "answers": [
                {"request_item_id": request_item_id, "answer": answer}
                for request_item_id, answer in pairs
            ]
        },
        ensure_ascii=False,
    )


def malformed_med057_like() -> str:
    return (
        '{"answers":[{"request_item_id":"RQ1","answer":"diagnosis",},'
        '{"request_item_id":"RQ2","answer":"differential",}}]}\n'
        "I made a JSON mistake. Let me rewrite it.\n"
        + response_json(("RQ1", "diagnosis"), ("RQ2", "differential"))
    )


def malformed_med058_like() -> str:
    return (
        '{"answers":[{"request_item_id":"RQ1","answer":"case analysis\n    },'
        '{"request_item_id":"RQ3","answer":"treatment content remains present"}]}'
    )


def worker_inputs(
    request_item_ids: list[str], *, worker: str = "diagnostic_agent"
) -> tuple[
    AgentDefinition,
    Subtask,
    RequestSpec,
    AnswerContract,
    TaskComplexityProfile,
    ResponseProfile,
    ProceduralSkill,
]:
    items = [
        RequestItem(
            request_item_id,
            f"Question {request_item_id}",
            True,
            index,
            f"Question {request_item_id}",
            "TREATMENT_PLAN" if request_item_id == "RQ3" else "DIAGNOSIS_WITH_BASIS",
        )
        for index, request_item_id in enumerate(request_item_ids, 1)
    ]
    request_spec = RequestSpec(items)
    request_spec.validate()
    deliverable = (
        "TREATMENT_PLAN" if request_item_ids == ["RQ3"] else "DIAGNOSIS_WITH_BASIS"
    )
    contract = AnswerContract(
        intent=deliverable.casefold(),
        requested_deliverables=[deliverable],
        must_cover=[deliverable.replace("_", " ").casefold()],
        coverage_checklist=[
            CoverageItem(item=deliverable.replace("_", " ").casefold())
        ],
    )
    contract.validate()
    subtask = Subtask(
        "ST1",
        "Return the assigned answers.",
        worker,
        [deliverable],
        request_item_ids=request_item_ids,
    )
    agent = AgentDefinition(worker, "Test worker.", "Assigned scope.", "Stay bounded.")
    complexity = TaskComplexityProfile(
        "focused",
        len(request_item_ids),
        worker == "diagnostic_agent",
        len(request_item_ids) > 1,
        worker == "consultation_agent",
        False,
        "none",
        False,
        False,
    )
    profile = ResponseProfile("standard", "Answer assigned deliverables.")
    skill = ProceduralSkill("test", "Follow the assigned task.", Path("test.md"))
    return agent, subtask, request_spec, contract, complexity, profile, skill


async def execute_worker(
    tmp_path: Path,
    responses: list[LLMResponse | BaseException],
    request_item_ids: list[str],
    *,
    worker: str = "diagnostic_agent",
    registry: ToolRegistry | None = None,
) -> tuple[Any, QueueLLM, list[dict[str, Any]], Subtask, RequestSpec, AnswerContract]:
    llm = QueueLLM(responses)
    tools = registry or ToolRegistry()
    agent, subtask, request_spec, contract, complexity, profile, skill = worker_inputs(
        request_item_ids, worker=worker
    )
    trace = TraceRecorder(tmp_path)
    result = await AgentLoop(
        llm,
        tools,
        max_tokens=8192,
        max_length_recoveries=1,
        max_infrastructure_retries=1,
    ).execute(
        agent,
        subtask,
        request_spec,
        contract,
        EvidenceLedger(),
        {"description": "must not enter recovery", "question": "original question"},
        [{"role": "user", "content": "memory must not enter recovery"}],
        skill,
        trace,
        complexity,
        profile,
    )
    return result, llm, read_trace(trace.run_dir), subtask, request_spec, contract


@pytest.mark.asyncio
async def test_valid_json_does_not_trigger_protocol_recovery(tmp_path: Path) -> None:
    result, llm, events, *_ = await execute_worker(
        tmp_path,
        [LLMResponse(response_json(("RQ1", "answer")), finish_reason="stop")],
        ["RQ1"],
    )

    assert result.success is True
    assert result.protocol_recovery_count == 0
    assert len(llm.requests) == 1
    assert not any("protocol_recovery" in event["event_type"] for event in events)


@pytest.mark.asyncio
async def test_existing_closing_tail_recovery_does_not_call_provider_again(
    tmp_path: Path,
) -> None:
    result, llm, events, *_ = await execute_worker(
        tmp_path,
        [LLMResponse(response_json(("RQ1", "answer")) + "]}", finish_reason="stop")],
        ["RQ1"],
    )

    assert result.success is True
    assert result.protocol_recovery_count == 0
    assert len(llm.requests) == 1
    draft = next(event for event in events if event["event_type"] == "worker_draft")
    assert draft["payload"]["parse_status"] == "recovered_json"


@pytest.mark.asyncio
async def test_med057_like_output_gets_one_bounded_protocol_recovery(
    tmp_path: Path,
) -> None:
    raw = malformed_med057_like()
    result, llm, events, *_ = await execute_worker(
        tmp_path,
        [
            LLMResponse(raw, finish_reason="stop"),
            LLMResponse(
                response_json(("RQ1", "diagnosis"), ("RQ2", "differential")),
                usage={
                    "prompt_tokens": 10,
                    "completion_tokens": 20,
                    "total_tokens": 30,
                    "completion_tokens_details": {"reasoning_tokens": 5},
                },
                finish_reason="stop",
            ),
        ],
        ["RQ1", "RQ2"],
    )

    assert result.success is True
    assert result.protocol_recovery_count == 1
    assert result.protocol_recovery_success_count == 1
    assert result.provider_attempt_count == 2
    assert [item.request_item_id for item in result.request_item_answers] == ["RQ1", "RQ2"]
    assert len(llm.requests) == 2
    recovery_request = llm.requests[1]
    assert recovery_request["tools"] == []
    assert recovery_request["temperature"] == 0.0
    assert len(recovery_request["messages"]) == 2
    payload = json.loads(recovery_request["messages"][1]["content"])
    assert set(payload) == {
        "malformed_worker_response",
        "allowed_request_item_ids",
        "required_schema",
    }
    assert payload["malformed_worker_response"] == raw
    assert payload["allowed_request_item_ids"] == ["RQ1", "RQ2"]
    assert "must not enter recovery" not in recovery_request["messages"][1]["content"]
    assert "memory must not enter recovery" not in recovery_request["messages"][1]["content"]
    assert sum(
        event["event_type"] == "worker_protocol_recovery_start" for event in events
    ) == 1
    recovery_result = next(
        event for event in events if event["event_type"] == "worker_protocol_recovery_result"
    )
    assert recovery_result["payload"]["provider_outcome"] == "success"
    assert recovery_result["payload"]["recovery_finish_reason"] == "stop"
    assert recovery_result["payload"]["recovery_parse_status"] == "direct_json"
    assert recovery_result["payload"]["usage"] == {
        "prompt_tokens": 10,
        "completion_tokens": 20,
        "reasoning_tokens": 5,
        "total_tokens": 30,
    }
    assert isinstance(recovery_result["payload"]["latency_ms"], float)


@pytest.mark.asyncio
async def test_med058_like_tool_result_is_not_replayed_during_recovery(
    tmp_path: Path,
) -> None:
    tool_invocations = 0

    def assess_risk(symptoms: str) -> dict[str, str]:
        nonlocal tool_invocations
        tool_invocations += 1
        return {"input": symptoms, "notice": "review"}

    registry = ToolRegistry()
    registry.register("assess_risk", assess_risk)
    result, llm, events, subtask, request_spec, contract = await execute_worker(
        tmp_path,
        [
            LLMResponse(
                "",
                [ToolCall("risk-1", "assess_risk", {"symptoms": "BMI 43.7"})],
                finish_reason="tool_calls",
            ),
            LLMResponse(malformed_med058_like(), finish_reason="stop"),
            LLMResponse(response_json(("RQ3", "treatment plan")), finish_reason="stop"),
        ],
        ["RQ3"],
        worker="consultation_agent",
        registry=registry,
    )

    assert result.success is True
    assert result.protocol_recovery_count == 1
    assert result.protocol_recovery_success_count == 1
    assert result.provider_attempt_count == 3
    assert [item.request_item_id for item in result.request_item_answers] == ["RQ3"]
    assert tool_invocations == 1
    assert registry.calls_for("consultation_agent") == 1
    assert len(llm.requests) == 3
    assert llm.requests[2]["tools"] == []
    assert sum(event["event_type"] == "tool_call" for event in events) == 1
    assert evaluate_request_coverage(request_spec, [result]).complete is True
    assert evaluate_contract_coverage(contract, [subtask], [result]).complete is True


@pytest.mark.asyncio
async def test_invalid_protocol_recovery_stops_after_one_attempt(tmp_path: Path) -> None:
    result, llm, events, subtask, request_spec, contract = await execute_worker(
        tmp_path,
        [
            LLMResponse(malformed_med057_like(), finish_reason="stop"),
            LLMResponse('{"answers":[', finish_reason="stop"),
        ],
        ["RQ1", "RQ2"],
    )

    assert result.success is False
    assert result.worker_status == "generation_error"
    assert result.protocol_recovery_count == 1
    assert result.protocol_recovery_success_count == 0
    assert len(llm.requests) == 2
    assert sum(
        event["event_type"] == "worker_protocol_recovery_start" for event in events
    ) == 1
    assert evaluate_request_coverage(request_spec, [result]).complete is False
    assert evaluate_contract_coverage(contract, [subtask], [result]).complete is False


@pytest.mark.asyncio
async def test_recovery_wrong_request_id_does_not_cover_assigned_item(
    tmp_path: Path,
) -> None:
    result, llm, _, subtask, request_spec, contract = await execute_worker(
        tmp_path,
        [
            LLMResponse(malformed_med058_like(), finish_reason="stop"),
            LLMResponse(response_json(("RQ1", "wrong scope")), finish_reason="stop"),
        ],
        ["RQ3"],
        worker="consultation_agent",
    )

    assert result.success is False
    assert result.request_item_answers == []
    assert result.protocol_recovery_count == 1
    assert len(llm.requests) == 2
    assert evaluate_request_coverage(request_spec, [result]).complete is False
    assert evaluate_contract_coverage(contract, [subtask], [result]).complete is False


@pytest.mark.asyncio
async def test_recovery_empty_answer_fails(tmp_path: Path) -> None:
    result, llm, *_ = await execute_worker(
        tmp_path,
        [
            LLMResponse(malformed_med058_like(), finish_reason="stop"),
            LLMResponse(response_json(("RQ3", "")), finish_reason="stop"),
        ],
        ["RQ3"],
        worker="consultation_agent",
    )

    assert result.success is False
    assert result.protocol_recovery_count == 1
    assert result.protocol_recovery_success_count == 0
    assert len(llm.requests) == 2


@pytest.mark.asyncio
async def test_recovery_plain_text_is_not_accepted_as_legacy_output(tmp_path: Path) -> None:
    result, llm, *_ = await execute_worker(
        tmp_path,
        [
            LLMResponse(malformed_med058_like(), finish_reason="stop"),
            LLMResponse("plain treatment text", finish_reason="stop"),
        ],
        ["RQ3"],
        worker="consultation_agent",
    )

    assert result.success is False
    assert result.protocol_recovery_count == 1
    assert result.protocol_recovery_success_count == 0
    assert len(llm.requests) == 2


@pytest.mark.asyncio
async def test_recovery_response_must_be_strict_json(tmp_path: Path) -> None:
    result, llm, *_ = await execute_worker(
        tmp_path,
        [
            LLMResponse(malformed_med058_like(), finish_reason="stop"),
            LLMResponse(
                response_json(("RQ3", "treatment")) + "]}", finish_reason="stop"
            ),
        ],
        ["RQ3"],
        worker="consultation_agent",
    )

    assert result.success is False
    assert result.protocol_recovery_count == 1
    assert result.protocol_recovery_success_count == 0
    assert len(llm.requests) == 2


@pytest.mark.asyncio
async def test_length_response_uses_existing_length_recovery_only(tmp_path: Path) -> None:
    result, llm, events, *_ = await execute_worker(
        tmp_path,
        [
            LLMResponse('{"answers":[', finish_reason="length"),
            LLMResponse(response_json(("RQ1", "complete")), finish_reason="stop"),
        ],
        ["RQ1"],
    )

    assert result.success is True
    assert result.length_recovery_count == 1
    assert result.protocol_recovery_count == 0
    assert len(llm.requests) == 2
    assert not any("protocol_recovery" in event["event_type"] for event in events)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [httpx.ConnectError("connect"), httpx.ReadTimeout("timeout")],
)
async def test_infrastructure_error_uses_existing_retry_only(
    tmp_path: Path, error: BaseException
) -> None:
    result, llm, events, *_ = await execute_worker(
        tmp_path,
        [error, LLMResponse(response_json(("RQ1", "complete")), finish_reason="stop")],
        ["RQ1"],
    )

    assert result.success is True
    assert result.infrastructure_retry_count == 1
    assert result.protocol_recovery_count == 0
    assert len(llm.requests) == 2
    assert not any("protocol_recovery" in event["event_type"] for event in events)


@pytest.mark.asyncio
async def test_recovery_tool_call_is_rejected_without_execution(tmp_path: Path) -> None:
    tool_invocations = 0

    def assess_risk(symptoms: str) -> dict[str, str]:
        nonlocal tool_invocations
        tool_invocations += 1
        return {"input": symptoms}

    registry = ToolRegistry()
    registry.register("assess_risk", assess_risk)
    result, llm, events, *_ = await execute_worker(
        tmp_path,
        [
            LLMResponse(malformed_med058_like(), finish_reason="stop"),
            LLMResponse(
                "",
                [ToolCall("forbidden", "assess_risk", {"symptoms": "x"})],
                finish_reason="tool_calls",
            ),
        ],
        ["RQ3"],
        worker="consultation_agent",
        registry=registry,
    )

    assert result.success is False
    assert result.protocol_recovery_count == 1
    assert result.protocol_recovery_success_count == 0
    assert tool_invocations == 0
    assert registry.calls_for("consultation_agent") == 0
    assert len(llm.requests) == 2
    assert llm.requests[1]["tools"] == []
    recovery_result = next(
        event for event in events if event["event_type"] == "worker_protocol_recovery_result"
    )
    assert recovery_result["payload"]["recovery_finish_reason"] == "tool_calls"
    assert recovery_result["payload"]["success"] is False
