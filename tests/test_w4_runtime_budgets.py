"""Offline counterexamples for task-local budgets and bounded recovery."""
import asyncio
import json
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from medagent.context.evidence_ledger import EvidenceLedger
from medagent.context.request_spec import build_request_spec
from medagent.llm.client import LLMProtocolError, LLMResponse, OpenAICompatibleLLM, ToolCall
from medagent.llm.fakes import ScriptedLLM
from medagent.observability.replay import read_trace
from medagent.observability.tracer import TraceRecorder
from medagent.planning.models import PlanBudgetError
from medagent.planning.planner import Planner
from medagent.runtime.agent_loop import AgentLoop
from medagent.runtime.config import RuntimeConfig
from medagent.runtime.engine import StageGenerationError
from medagent.runtime.native_engine import NativeMedAgentEngine
from medagent.tools.registry import ToolExecutionContext, ToolRegistry
from tests.test_w3_security import PLAN, PRIVATE
from tests.test_worker_protocol_recovery import QueueLLM, response_json, worker_inputs


def tool(index=0):
    return LLMResponse(tool_calls=[ToolCall(str(index), "assess_risk", {"symptoms": "Synthetic"})],
                       finish_reason="tool_calls")


def answer():
    return LLMResponse(response_json(("RQ1", "A provisional assessment.")), finish_reason="stop")


async def run_loop(tmp_path, responses, *, rounds=8, tools=2, retries=2, handler=None):
    llm = QueueLLM(responses)
    registry = ToolRegistry(max_calls=tools)
    effects = []
    registry.register("assess_risk", handler or (lambda symptoms: effects.append(symptoms) or {}))
    agent, task, spec, contract, complexity, profile, skill = worker_inputs(["RQ1"])
    trace = TraceRecorder(tmp_path)
    result = await AgentLoop(llm, registry, tools, max_model_rounds=rounds,
        max_infrastructure_retries=retries, infrastructure_retry_base_delay_seconds=0).execute(
        agent, task, spec, contract, EvidenceLedger(), {"description": "Synthetic"}, [],
        skill, trace, complexity, profile,
    )
    return result, llm, effects, read_trace(trace.run_dir)


@pytest.mark.asyncio
@pytest.mark.parametrize("rounds,tools,calls,effects,reason", [
    (2, 16, 2, 2, "model_round_budget_exhausted"),
    (8, 2, 3, 2, "tool_call_budget_exhausted"),
    (8, 0, 1, 0, "tool_call_budget_exhausted"),
])
async def test_continuous_tool_requests_terminate(tmp_path, rounds, tools, calls, effects, reason):
    result, llm, executed, events = await run_loop(
        tmp_path, [tool(i) for i in range(12)], rounds=rounds, tools=tools,
    )
    assert not result.success and result.worker_status == "budget_exhausted"
    assert result.failure_reason == reason
    assert len(llm.requests) == result.model_round_count == calls
    assert len(executed) == result.tool_calls == effects
    assert sum(e["event_type"] == "worker_model_round" for e in events) == calls
    assert events[-1]["payload"]["failure_reason"] == reason


@pytest.mark.asyncio
async def test_oversized_tool_batch_does_not_execute_extra_side_effects(tmp_path):
    batch = LLMResponse(tool_calls=[tool(i).tool_calls[0] for i in range(5)],
                        finish_reason="tool_calls")
    result, llm, effects, _ = await run_loop(tmp_path, [batch, answer()], tools=1)
    assert not result.success and result.failure_reason == "tool_call_budget_exhausted"
    assert len(effects) == len(llm.requests) == 1


@pytest.mark.asyncio
async def test_retries_share_physical_model_round_cap(tmp_path):
    result, llm, effects, _ = await run_loop(tmp_path,
        [httpx.ConnectError(PRIVATE)] * 5, rounds=2, retries=3)
    assert len(llm.requests) == result.model_round_count == 2
    assert result.worker_status == "budget_exhausted" and not effects
    assert result.infrastructure_retry_count == 1


@pytest.mark.asyncio
async def test_retry_after_tool_does_not_replay_side_effects(tmp_path):
    result, llm, effects, _ = await run_loop(tmp_path,
        [tool(), httpx.ConnectError(PRIVATE), answer()])
    assert result.success and len(effects) == 1
    assert result.model_round_count == result.provider_attempt_count == len(llm.requests) == 3
    assert result.infrastructure_retry_count == 1


@pytest.mark.asyncio
async def test_infrastructure_retry_allowance_is_per_task_not_per_turn(tmp_path):
    result, llm, effects, _ = await run_loop(tmp_path,
        [httpx.ConnectError(PRIVATE), tool(), httpx.ConnectError(PRIVATE), answer()], retries=1)
    assert not result.success and result.worker_status == "provider_error"
    assert len(llm.requests) == result.model_round_count == 3
    assert result.infrastructure_retry_count == 1 and len(effects) == 1


@pytest.mark.asyncio
async def test_length_recovery_allowance_is_per_task_not_per_turn(tmp_path):
    short = LLMResponse('{"answers":[', finish_reason="length")
    result, llm, effects, _ = await run_loop(tmp_path, [short, tool(), short, answer()])
    assert not result.success and result.worker_status == "budget_exhausted"
    assert result.length_recovery_count == 1 and len(llm.requests) == 3 and len(effects) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("rounds,expected", [(1, "budget_exhausted"), (8, "protocol_error")])
async def test_protocol_repair_is_bounded_and_charged_to_model_rounds(tmp_path, rounds, expected):
    malformed = LLMResponse('{"answers":[', finish_reason="stop")
    result, llm, _, _ = await run_loop(tmp_path, [malformed, malformed, answer()], rounds=rounds)
    assert not result.success and result.worker_status == expected
    assert result.model_round_count == len(llm.requests) == min(rounds, 2)
    assert result.protocol_recovery_count == int(rounds > 1)


@pytest.mark.asyncio
async def test_provider_failure_during_protocol_repair_keeps_provider_class(tmp_path):
    malformed = LLMResponse('{"answers":[', finish_reason="stop")
    result, llm, _, _ = await run_loop(tmp_path, [malformed, httpx.ConnectError(PRIVATE)])
    assert not result.success and result.worker_status == "provider_error"
    assert len(llm.requests) == result.model_round_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("body", ["not JSON", '{"choices":[]}', '{"choices":[{}]}'])
async def test_invalid_provider_envelope_is_protocol_error_without_body_leak(tmp_path, body):
    client = OpenAICompatibleLLM(base_url="http://offline.invalid", api_key="synthetic-key",
                                 model="offline")
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, text=body, request=request)))
    try:
        with pytest.raises(LLMProtocolError, match="invalid provider response envelope"):
            await client.complete([])
    finally:
        await client.close()
    result, llm, _, events = await run_loop(tmp_path, [LLMProtocolError(PRIVATE), answer()])
    assert result.worker_status == "protocol_error" and len(llm.requests) == 1
    assert PRIVATE not in json.dumps(events)


@pytest.mark.asyncio
async def test_nonretryable_provider_error_does_not_consume_retry_loop(tmp_path):
    request = httpx.Request("POST", "http://offline.invalid")
    response = httpx.Response(401, request=request)
    error = httpx.HTTPStatusError(PRIVATE, request=request, response=response)
    result, llm, _, _ = await run_loop(tmp_path, [error, answer()])
    assert result.worker_status == "provider_error" and len(llm.requests) == 1
    assert result.infrastructure_retry_count == 0


@pytest.mark.asyncio
async def test_tool_exception_is_observable_without_provider_misclassification(tmp_path):
    def fail(symptoms):
        raise ValueError(PRIVATE)
    result, llm, _, events = await run_loop(tmp_path, [tool(), answer()], handler=fail)
    assert result.success and result.tool_error_count == 1 and len(llm.requests) == 2
    tool_result = next(e for e in events if e["event_type"] == "tool_result")
    assert tool_result["payload"]["status"] == "tool_error"
    assert tool_result["payload"]["result"]["error_type"] == "ValueError"
    assert PRIVATE not in json.dumps(events)


@pytest.mark.asyncio
async def test_same_role_task_budgets_are_independent_and_role_totals_remain_available():
    registry = ToolRegistry(max_calls=1)
    effects = []
    registry.register("assess_risk", lambda symptoms: effects.append(symptoms))
    for name in ["a", "b"]:
        ctx = ToolExecutionContext(name, "Assess", (), ())
        await registry.execute("diagnostic_agent", "assess_risk", {"symptoms": name}, context=ctx)
        with pytest.raises(RuntimeError, match="budget exhausted"):
            await registry.execute(
                "diagnostic_agent", "assess_risk", {"symptoms": name}, context=ctx,
            )
        assert registry.calls_for("diagnostic_agent", subtask_id=name) == 1
    assert effects == ["a", "b"] and registry.calls_for("diagnostic_agent") == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("dependent", [False, True])
async def test_oversized_plan_is_rejected_before_any_worker_or_fallback(tmp_path, dependent):
    tasks = [{"subtask_id": f"t{i}", "description": "Assess", "assigned_agent": "diagnostic_agent",
              "depends_on": ["t0"] if dependent and i else []} for i in range(2)]
    with pytest.raises(PlanBudgetError):
        Planner.parse({"subtasks": tasks}, "diagnostic_agent", max_subtasks=1)
    llm = ScriptedLLM([{"subtasks": tasks}, "must not run"])
    engine = NativeMedAgentEngine(RuntimeConfig(trace_dir=str(tmp_path), max_subtasks=1,
                                 semantic_validation_enabled=False), llm=llm)
    try:
        with pytest.raises(StageGenerationError) as raised:
            await engine.analyze("Synthetic", "Assess")
    finally:
        await engine.close()
    assert raised.value.failure_reason == "subtask_budget_exhausted"
    assert len(llm.requests) == 1
    run_dir, = tmp_path.iterdir()
    events = read_trace(run_dir)
    assert not any(e["event_type"] in {"worker_draft", "synthesis_input"} for e in events)
    assert events[-2]["payload"]["failure_reason"] == "subtask_budget_exhausted"


@pytest.mark.asyncio
async def test_task_budget_does_not_truncate_multi_item_contract(tmp_path):
    llm = ScriptedLLM([PLAN, {"answers": [
        {"request_item_id": "RQ1", "answer": "DIAGNOSIS WITH BASIS: A provisional assessment."},
        {"request_item_id": "RQ2", "answer": "TREATMENT PLAN: Seek qualified assessment."},
    ]}])
    engine = NativeMedAgentEngine(RuntimeConfig(trace_dir=str(tmp_path), max_subtasks=1,
                                 semantic_validation_enabled=False), llm=llm)
    try:
        result = await engine.analyze(
            "Synthetic", "1. Give a diagnosis.\n2. Give a treatment plan.",
        )
    finally:
        await engine.close()
    assert result["status"] == "completed" and len(llm.requests) == 2
    assert set(result["final_request_item_answers"]) == {"RQ1", "RQ2"}


@pytest.mark.asyncio
async def test_worker_timeout_returns_explainable_incomplete_result(tmp_path):
    class Slow(ScriptedLLM):
        async def complete(self, messages, **kwargs):
            if messages[0]["content"].startswith("Role:"):
                await asyncio.sleep(1)
            return await super().complete(messages, **kwargs)
    engine = NativeMedAgentEngine(RuntimeConfig(trace_dir=str(tmp_path), worker_timeout_seconds=.02,
                                 semantic_validation_enabled=False), llm=Slow([PLAN]))
    try:
        result = await engine.analyze("Synthetic", "Assess")
    finally:
        await engine.close()
    assert result["status"] == "incomplete"
    assert result["execution_failures"][0]["worker_status"] == "worker_timeout"
    assert result["trace"]["execution_failures"] == result["execution_failures"]
    assert result["execution_failures"][0]["model_round_count"] == 1


@pytest.mark.asyncio
async def test_optional_only_request_cannot_complete_when_all_workers_fail(tmp_path):
    class Failing(ScriptedLLM):
        async def complete(self, messages, **kwargs):
            if messages[0]["content"].startswith("Role:"):
                raise httpx.ConnectError(PRIVATE)
            return await super().complete(messages, **kwargs)
    question = "Optional: explain causes."
    assert not build_request_spec(question).required_item_ids
    engine = NativeMedAgentEngine(RuntimeConfig(trace_dir=str(tmp_path),
        worker_max_infrastructure_retries=0, semantic_validation_enabled=False),
        llm=Failing([PLAN]))
    try:
        result = await engine.analyze("Synthetic", question)
    finally:
        await engine.close()
    assert result["status"] == "incomplete" and not result["user_request_complete"]
    assert not result["trace"]["user_request_complete"] and not result["contract_complete"]
    assert result["execution_failures"][0]["worker_status"] == "provider_error"


@pytest.mark.asyncio
async def test_timeout_after_tool_and_retry_preserves_attempt_counters(tmp_path):
    class SlowRetry(ScriptedLLM):
        worker_calls = 0

        async def complete(self, messages, **kwargs):
            if not messages[0]["content"].startswith("Role:"):
                return await super().complete(messages, **kwargs)
            self.worker_calls += 1
            if self.worker_calls == 1:
                return tool()
            if self.worker_calls == 2:
                raise httpx.ConnectError(PRIVATE)
            await asyncio.sleep(1)
            return answer()

    llm = SlowRetry([PLAN])
    engine = NativeMedAgentEngine(RuntimeConfig(trace_dir=str(tmp_path), worker_timeout_seconds=.05,
        worker_infrastructure_retry_base_delay_seconds=0, semantic_validation_enabled=False),
        llm=llm)
    try:
        result = await engine.analyze("Synthetic", "Assess")
    finally:
        await engine.close()
    events = read_trace(tmp_path / result["run_id"])
    worker = next(e["payload"] for e in events if e["event_type"] == "worker_draft")
    assert worker["worker_status"] == "worker_timeout"
    assert worker["model_round_count"] == worker["provider_attempt_count"] == 3
    assert worker["infrastructure_retry_count"] == worker["tool_calls"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrency", [1, 2])
async def test_same_role_parallel_roots_get_independent_tools_and_bounded_slots(
    tmp_path, concurrency, monkeypatch,
):
    tasks = [{"subtask_id": name, "description": "Assess diagnosis",
              "assigned_agent": "diagnostic_agent", "request_item_ids": ["RQ1"],
              "deliverable_ids": ["DIAGNOSIS_WITH_BASIS"], "depends_on": deps}
             for name, deps in [("a", []), ("b", []), ("tail", ["a", "b"])]]

    class Concurrent(ScriptedLLM):
        active = 0
        peak = 0

        async def complete(self, messages, **kwargs):
            if not messages[0]["content"].startswith("Role:"):
                return await super().complete(messages, **kwargs)
            self.active += 1
            self.peak = max(self.peak, self.active)
            try:
                await asyncio.sleep(.01)
                if not any(m["role"] == "tool" for m in messages):
                    return tool(json.loads(messages[1]["content"])["subtask"]["subtask_id"])
                return answer()
            finally:
                self.active -= 1

    effects = []
    registry = ToolRegistry(max_calls=1)
    registry.register("assess_risk", lambda ctx, symptoms: effects.append(ctx.subtask_id) or {},
                      with_context=True)
    llm = Concurrent([{"subtasks": tasks}, json.loads(answer().content)])
    engine = NativeMedAgentEngine(RuntimeConfig(trace_dir=str(tmp_path), max_tool_calls=1,
        max_concurrent_workers=concurrency, semantic_validation_enabled=False), llm=llm)
    monkeypatch.setattr(engine, "_tool_registry", lambda *args: registry)
    try:
        result = await asyncio.wait_for(engine.analyze("Synthetic", "diagnosis"), 2)
    finally:
        await engine.close()
    assert result["status"] == "completed" and llm.peak == concurrency
    assert sorted(effects) == ["a", "b", "tail"]
    assert registry.calls_for("diagnostic_agent") == 3
    assert all(registry.calls_for("diagnostic_agent", subtask_id=name) == 1
               for name in ["a", "b", "tail"])


def test_api_reports_budget_and_worker_failures_without_exception_body(tmp_path, monkeypatch):
    from api import main
    cfg = RuntimeConfig(trace_dir=str(tmp_path), semantic_validation_enabled=False, max_subtasks=1)
    oversized = {"subtasks": [{"description": "Assess", "assigned_agent": "diagnostic_agent"}] * 2}
    engine = NativeMedAgentEngine(cfg, llm=ScriptedLLM([oversized]))
    monkeypatch.setattr(main, "_coordinator", engine)
    monkeypatch.setattr(main, "_config", cfg)
    monkeypatch.setattr(main, "_access_token", "")
    with TestClient(main.app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        response = client.post("/api/analyze", json={"description": PRIVATE, "question": "Assess"})
        assert response.status_code == 422 and PRIVATE not in response.text
        assert response.json()["detail"]["failure_reason"] == "subtask_budget_exhausted"


def test_api_worker_failure_and_run_query_keep_safe_diagnostics(tmp_path, monkeypatch):
    from api import main
    cfg = RuntimeConfig(trace_dir=str(tmp_path), semantic_validation_enabled=False,
                        max_tool_calls=0)
    engine = NativeMedAgentEngine(cfg, llm=ScriptedLLM([PLAN, tool()]))
    monkeypatch.setattr(main, "_coordinator", engine)
    monkeypatch.setattr(main, "_config", cfg)
    monkeypatch.setattr(main, "_access_token", "")
    monkeypatch.setattr(main, "_run_sessions", {})
    with TestClient(main.app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        response = client.post("/api/analyze", json={"description": PRIVATE, "question": "Assess"})
        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "incomplete"
        failure = result["execution_failures"][0]
        assert failure["failure_reason"] == "tool_call_budget_exhausted"
        assert failure["required_request_items"] == ["RQ1"] and failure["task_index"] == 1
        query = client.get('/api/runs/' + result["run_id"],
                           params={"session_id": result["session_id"]})
        assert query.status_code == 200 and query.json()["execution_failures"] == [failure]
        assert PRIVATE not in query.text


@pytest.mark.parametrize("field,value", [("worker_max_model_rounds", 0), ("max_subtasks", True),
    ("max_concurrent_workers", 0), ("max_tool_calls", -1),
    ("worker_timeout_seconds", float("inf")), ("llm_timeout_seconds", float("nan")),
    ("worker_max_length_recoveries", 2)])
def test_invalid_budgets_are_rejected(field, value):
    with pytest.raises(ValueError):
        replace(RuntimeConfig(), **{field: value})
