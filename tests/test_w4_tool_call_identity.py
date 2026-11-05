"""Single-task tool identity: offline side effects are counted explicitly."""
import asyncio
import json
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from medagent.context.evidence_ledger import EvidenceLedger
from medagent.llm.client import LLMResponse, OpenAICompatibleLLM, ToolCall
from medagent.llm.fakes import ScriptedLLM
from medagent.observability.replay import read_trace
from medagent.observability.tracer import TraceRecorder
from medagent.runtime.agent_loop import AgentLoop
from medagent.runtime.config import RuntimeConfig
from medagent.runtime.native_engine import NativeMedAgentEngine
from medagent.tools.registry import ToolRegistry
from tests.test_release_integration import OfflineFailureLLM
from tests.test_semantic_validation import inventory
from tests.test_w3_security import PLAN, PRIVATE
from tests.test_w4_runtime_budgets import answer, tool
from tests.test_worker_protocol_recovery import QueueLLM, worker_inputs


async def execute(tmp_path, responses, *, budget=2, fail_handler=False, registry=None,
                  effects=None, task_id="task", client=None):
    effects = [] if effects is None else effects
    if registry is None:
        registry = ToolRegistry(max_calls=budget)

        def handle(name, symptoms):
            effects.append((name, symptoms))
            if fail_handler:
                raise ValueError(PRIVATE)
            return {"ok": True}

        registry.register("assess_risk", lambda symptoms: handle("assess_risk", symptoms))
        registry.register("analyze_symptoms", lambda symptoms: handle("analyze_symptoms", symptoms))
    llm = client or QueueLLM(responses)
    agent, task, spec, contract, complexity, profile, skill = worker_inputs(["RQ1"])
    task = replace(task, subtask_id=task_id)
    trace = TraceRecorder(tmp_path)
    result = await AgentLoop(llm, registry, budget,
        infrastructure_retry_base_delay_seconds=0).execute(
        agent, task, spec, contract, EvidenceLedger(), {}, [], skill, trace, complexity, profile,
    )
    return result, llm, effects, read_trace(trace.run_dir)


def assert_protocol_failure(result, events, reason):
    assert not result.success and result.worker_status == "protocol_error"
    assert result.generation_status == "protocol_error" and result.failure_reason == reason
    draft = events[-1]
    assert draft["event_type"] == "worker_draft"
    assert draft["payload"]["worker_status"] == "protocol_error"
    assert draft["payload"]["failure_reason"] == reason
    parent = next(e for e in events if e["event_id"] == draft["parent_event_id"])
    assert parent["event_type"] == "llm_response"


@pytest.mark.asyncio
async def test_duplicate_batch_is_rejected_before_any_handler(tmp_path):
    batch = tool(PRIVATE)
    batch.tool_calls.append(tool(PRIVATE).tool_calls[0])
    result, llm, effects, events = await execute(tmp_path, [batch, answer()])
    assert effects == []  # A: both calls are rejected before dispatch.
    assert result.tool_calls == 0 and len(llm.requests) == result.model_round_count == 1
    assert_protocol_failure(result, events, "duplicate_tool_call_id")
    assert [m["role"] for m in llm.requests[-1]["messages"]] == ["system", "user"]
    assert PRIVATE not in json.dumps(events)


@pytest.mark.asyncio
@pytest.mark.parametrize("budget", [1, 2, 16])
async def test_replayed_id_after_infrastructure_retry_never_repeats_handler(tmp_path, budget):
    result, llm, effects, events = await execute(tmp_path,
        [tool(PRIVATE), httpx.ConnectError(PRIVATE), tool(PRIVATE), answer()], budget=budget)
    assert len(effects) == 1  # B/G: a larger allowance cannot authorize the same ID again.
    assert result.tool_calls == 1 and result.infrastructure_retry_count == 1
    assert len(llm.requests) == result.model_round_count == result.provider_attempt_count == 3
    assert_protocol_failure(result, events, "duplicate_tool_call_id")
    messages = llm.requests[-1]["messages"]
    assistant_ids = [c["id"] for m in messages if m["role"] == "assistant"
                     for c in m.get("tool_calls", [])]
    assert assistant_ids == [PRIVATE]
    assert [m["tool_call_id"] for m in messages if m["role"] == "tool"] == assistant_ids
    assert PRIVATE not in json.dumps(events)


@pytest.mark.asyncio
async def test_handler_that_mutates_then_raises_keeps_its_id_reserved(tmp_path):
    result, llm, effects, events = await execute(tmp_path,
        [tool("same"), tool("same"), answer()], fail_handler=True)
    assert len(effects) == 1  # C: failure after mutation must not release the reservation.
    assert result.tool_error_count == result.tool_calls == 1 and len(llm.requests) == 2
    assert_protocol_failure(result, events, "duplicate_tool_call_id")
    assert PRIVATE not in json.dumps(events)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["name", "arguments"])
async def test_same_id_with_changed_operation_is_rejected(tmp_path, change):
    changed = tool("same")
    if change == "name":
        changed.tool_calls[0].name = "analyze_symptoms"
    else:
        changed.tool_calls[0].arguments = {"symptoms": "Different"}
    result, llm, effects, events = await execute(tmp_path, [tool("same"), changed, answer()])
    assert effects == [("assess_risk", "Synthetic")]  # D: neither changed operation executes.
    assert len(llm.requests) == 2 and result.tool_calls == 1
    assert_protocol_failure(result, events, "duplicate_tool_call_id")


@pytest.mark.asyncio
@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("ids", [("first", "second"), ("call_A", "call_a")])
async def test_different_ids_with_identical_arguments_are_independent(tmp_path, batch, ids):
    first, second = (tool(value) for value in ids)
    if batch:
        first.tool_calls.extend(second.tool_calls)
    responses = [first, answer()] if batch else [first, second, answer()]
    result, llm, effects, _ = await execute(tmp_path, responses)
    assert effects == [("assess_risk", "Synthetic")] * 2  # E: no parameter-based deduplication.
    assert result.success and result.tool_calls == 2
    assert len(llm.requests) == (2 if batch else 3)


@pytest.mark.asyncio
@pytest.mark.parametrize("rejection", ["schema", "permission"])
async def test_rejected_attempt_also_reserves_id_without_executing_handler(tmp_path, rejection):
    rejected = tool("same")
    if rejection == "schema":
        rejected.tool_calls[0].arguments = {"unexpected": "Synthetic"}
    else:
        rejected.tool_calls[0].name = "shell"
    result, llm, effects, events = await execute(tmp_path, [rejected, tool("same"), answer()])
    assert effects == []  # Both the rejected attempt and reused ID invoke zero handlers.
    assert result.tool_calls == result.tool_error_count == 1 and len(llm.requests) == 2
    assert_protocol_failure(result, events, "duplicate_tool_call_id")


@pytest.mark.asyncio
async def test_concurrent_same_role_tasks_have_independent_call_id_spaces(tmp_path):
    effects = []
    registry = ToolRegistry(max_calls=1)
    registry.register("assess_risk", lambda symptoms: effects.append(symptoms) or {})
    outputs = await asyncio.gather(*[
        execute(tmp_path / name, [tool("same"), answer()], budget=1, task_id=name,
                registry=registry, effects=effects) for name in ["a", "b"]
    ])
    assert effects == ["Synthetic", "Synthetic"]  # F: one actual handler execution per task.
    assert all(output[0].success and output[0].tool_calls == 1 for output in outputs)
    assert registry.calls_for("diagnostic_agent") == 2
    assert all(registry.calls_for("diagnostic_agent", subtask_id=name) == 1 for name in ["a", "b"])


def test_api_and_metadata_report_replay_as_protocol_failure(tmp_path, monkeypatch):
    from api import main

    effects = []
    registry = ToolRegistry(max_calls=2)
    registry.register("assess_risk", lambda symptoms: effects.append(symptoms) or {})
    llm = OfflineFailureLLM([PLAN, tool(PRIVATE), httpx.ConnectError(PRIVATE),
                             tool(PRIVATE), answer()])
    extract = inventory("Assess", [("Assess", "required")])
    checker = ScriptedLLM([extract, extract])
    cfg = RuntimeConfig(trace_dir=str(tmp_path), worker_infrastructure_retry_base_delay_seconds=0)
    engine = NativeMedAgentEngine(cfg, llm=llm, validation_llm=checker)
    monkeypatch.setattr(engine, "_tool_registry", lambda *args: registry)
    monkeypatch.setattr(main, "_coordinator", engine)
    monkeypatch.setattr(main, "_config", cfg)
    monkeypatch.setattr(main, "_access_token", "")
    monkeypatch.setattr(main, "_run_sessions", {})
    with TestClient(main.app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        response = client.post("/api/analyze", json={
            "description": "Synthetic", "question": "Assess",
        })
        assert response.status_code == 200 and len(effects) == 1  # H: count the real handler.
        result = response.json()
        assert result["status"] == "incomplete" and not result["user_request_complete"]
        failure = result["execution_failures"][0]
        assert failure["worker_status"] == "protocol_error"
        assert failure["failure_reason"] == "duplicate_tool_call_id"
        assert failure["model_round_count"] == 3 and failure["required_request_items"] == ["RQ1"]
        assert result["semantic_validation"]["complete"] is False and len(checker.requests) == 2
        assert len(llm.requests) == 4  # Planner + 3 Worker attempts, no repair/synthesis.
        query = client.get('/api/runs/' + result["run_id"],
                           params={"session_id": result["session_id"]})
        assert query.status_code == 200 and query.json()["execution_failures"] == [failure]
        assert PRIVATE not in response.text and PRIVATE not in query.text
    disk = (tmp_path / result["run_id"] / "trace.jsonl").read_text("utf-8")
    assert PRIVATE not in disk
    events = read_trace(tmp_path / result["run_id"])
    assert sum(e["event_type"] == "tool_call" for e in events) == 1
    assert sum(e["event_type"] == "tool_result" for e in events) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, "", " ", "a b", "a\nb", "a\x00b", "a\u200bb",
                                  42, {}, [], "x" * 257])
async def test_invalid_tool_ids_terminate_before_handler_or_continuation(tmp_path, value):
    invalid = LLMResponse(tool_calls=[ToolCall(value, "assess_risk", {"symptoms": "Synthetic"})],
                          finish_reason="tool_calls")
    result, llm, effects, events = await execute(tmp_path, [invalid, answer()])
    assert effects == [] and result.tool_calls == 0
    assert len(llm.requests) == result.model_round_count == 1
    assert_protocol_failure(result, events, "invalid_tool_call_id")


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["call_1", "functions.assess_risk:0", "调用-1"])
async def test_valid_ids_remain_opaque_case_sensitive_protocol_data(tmp_path, value):
    result, llm, effects, _ = await execute(tmp_path, [tool(value), answer()])
    assert len(effects) == 1 and result.success and len(llm.requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("field", [{}, {"id": None}, {"id": ""}, {"id": 42}])
async def test_provider_adapter_does_not_fabricate_or_coerce_invalid_ids(tmp_path, field):
    requests = []
    client = OpenAICompatibleLLM(base_url="http://offline.invalid", api_key="synthetic-key",
                                 model="offline")
    await client._client.aclose()

    def respond(request):
        requests.append(request)
        call = {**field, "type": "function", "function": {
            "name": "assess_risk", "arguments": '{"symptoms":"Synthetic"}',
        }}
        return httpx.Response(200, request=request, json={"choices": [{
            "message": {"content": "", "tool_calls": [call]}, "finish_reason": "tool_calls",
        }]})

    client._client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    try:
        result, _, effects, events = await execute(tmp_path, [], client=client)
    finally:
        await client.close()
    assert effects == [] and len(requests) == result.model_round_count == 1
    assert not result.success and result.worker_status == "protocol_error"
    assert result.failure_reason == "invalid_tool_call_id"
    assert events[-1]["payload"]["failure_reason"] == "invalid_tool_call_id"
