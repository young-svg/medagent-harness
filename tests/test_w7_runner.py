"""Real offline adapters, model/tool boundaries and Native plan replay; no providers."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from eval.w7.budget import MeteredLLM, MeteredTools, RunBudget, RunBudgetExceeded
from eval.w7.data import ROOT, SMOKE_CASES, load_case
from eval.w7.runner import independent_score, run_case
from eval.w7.smoke import shared_cost
from medagent.llm import LLMResponse, ScriptedLLM
from medagent.observability.replay import read_trace
from medagent.runtime.acceptance import FinalAcceptance
from medagent.tools.registry import ToolRegistry
from medagent.tools.schemas import TOOL_SCHEMAS, ToolArgumentError


@pytest.mark.asyncio
@pytest.mark.parametrize("case_id", SMOKE_CASES)
async def test_all_architectures_execute_real_shared_gates_and_plan_replay(
    tmp_path, monkeypatch, case_id
):
    actual = FinalAcceptance.evaluate
    calls = []

    async def observed(self, **kwargs):
        calls.append(kwargs)
        return await actual(self, **kwargs)

    monkeypatch.setattr(FinalAcceptance, "evaluate", observed)
    records = {}
    for group in ("A", "B", "C"):
        row, plan = await run_case(case_id, group, tmp_path)
        records[group] = row
    records["C-serial"], replay = await run_case(
        case_id, "C-serial", tmp_path, replay=plan, shared_plan_cost=shared_cost(records["C"])
    )
    assert len(calls) == 4
    assert all(c["validator"].calls == 1 for c in calls)
    assert all(r["status"] == "completed" and r["acceptance_observed"] for r in records.values())
    assert all(r["independent_score"]["grade"] is None for r in records.values())
    assert len({r["request_sha256"] for r in records.values()}) == 1
    assert replay == plan
    assert records["C"]["conditions_sha256"] == records["C-serial"]["conditions_sha256"]
    for group, row in records.items():
        assert row["metrics"]["by_stage"].get("planning", 0) == int(group in {"B", "C"})
        assert row["metrics"]["by_stage"]["acceptance"] == 1
        assert row["metrics"]["input_tokens"] == "UNKNOWN"
        assert row["metrics"]["unknown_usage_requests"] == row["metrics"]["model_attempts"]
        assert row["metrics"]["peak_worker_contexts"] <= (4 if group == "C" else 1)
        assert row["dag_audit"]["violations"] == []
        assert row["metrics"]["by_stage"]["worker"] <= 8
        assert row["metrics"]["model_attempts"] <= 16
        assert row["metrics"]["tool_attempts"] <= 8
        assert all(m["status"] == "success" for m in row["models"])
        events = read_trace(tmp_path / row["trace_path"])
        ids = {e["event_id"] for e in events}
        assert all(e["parent_event_id"] is None or e["parent_event_id"] in ids for e in events)
        assert events[-1]["event_type"] == "run_end"
        assert all("prompt" not in e["payload"] for e in events)
    assert records["C-serial"]["shared_plan_generation_cost"]["model_attempts"] == 1
    if case_id in {"I03", "D03"}:
        assert records["C"]["metrics"]["peak_worker_contexts"] == 2
        assert records["B"]["worker_counts"]["execution_role_count"] == 1
        assert records["C"]["worker_counts"]["execution_role_count"] == 2
        assert records["B"]["metrics"]["by_stage"].get("synthesis", 0) == 0
        assert records["C"]["metrics"]["by_stage"]["synthesis"] == 1
    if case_id == "D03":
        for group in ("B", "C", "C-serial"):
            downstream = next(p for p in records[group]["worker_inputs"] if p["task_id"] == "RQ3")
            assert set(downstream["upstream_task_ids"]) == {"RQ1", "RQ2"}
    if case_id == "E03":
        for row in records.values():
            assert row["metrics"]["tool_attempts"] == row["metrics"]["handler_executions"] == 2
            assert [t["name"] for t in row["tools"]] == ["disease_code", "search_knowledge"]
            assert all(t["handler_executions"] == 1 for t in row["tools"])
            checks = row["result"]["evidence_validation"]["items"]
            assert [c["status"] for c in checks] == ["satisfied", "satisfied"]
            assert checks[0]["source_event_ids"] != checks[1]["source_event_ids"]
            assert checks[0]["origin_request_item_ids"] == ["RQ1"]
            assert checks[1]["origin_request_item_ids"] == ["RQ2"]
            boundaries = [m for m in row["models"] if m["stage"] == "worker"]
            assert boundaries[0]["tool_schema_names"] == ["disease_code", "search_knowledge"]
            # Native per-task tool attempts are exhausted after the two calls;
            # later continuation correctly removes tools instead of granting more.
            assert all(
                set(m["tool_schema_names"]) <= {"disease_code", "search_knowledge"}
                for m in boundaries
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("group", ["A", "B", "C", "C-serial"])
async def test_memory_off_is_real_empty_context_and_not_quality_pass(tmp_path, group):
    replay = None
    if group == "C-serial":
        _, replay = await run_case("M01", "C", tmp_path / "owner", memory_on=False)
    row, _ = await run_case("M01", group, tmp_path / "off", memory_on=False, replay=replay)
    assert row["status"] == "incomplete"
    assert all(p["history_messages"] == 0 for p in row["worker_inputs"])
    assert row["rq_acceptance"][0]["status"] == "insufficient_information"
    assert row["independent_score"]["grade"] is None
    assert "unavailable" in row["result"]["final_request_item_answers"]["RQ1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("group", ["B", "C", "C-serial"])
async def test_d03_real_transport_failure_retries_and_blocks_fan_in(tmp_path, group):
    replay = None
    if group == "C-serial":
        _, replay = await run_case("D03", "C", tmp_path / "owner", fault="upstream_failure")
    row, _ = await run_case(
        "D03", group, tmp_path / "failure", fault="upstream_failure", replay=replay
    )
    assert row["status"] == "incomplete"
    attempts = [m for m in row["models"] if m["task_id"] == "RQ1"]
    assert len(attempts) == 3 and all(m["status"] == "failed" for m in attempts)
    assert all(m["input_tokens"] == "UNKNOWN" for m in attempts)
    assert not any(m["task_id"] == "RQ3" for m in row["models"])
    assert row["worker_counts"]["blocked"] == 1
    assert row["metrics"]["failed_requests"] == 3
    assert row["metrics"]["handler_executions"] == 0
    assert row["acceptance_observed"]
    assert row["recovery_events"]


@pytest.mark.asyncio
@pytest.mark.parametrize("group", ["A", "B", "C"])
@pytest.mark.parametrize(
    "fault",
    ["classification_only", "empty", "duplicate_id", "swapped_citations", "replay_after_retry"],
)
async def test_e03_actual_handler_counts_wrong_sources_and_protocol_failures(
    tmp_path, group, fault
):
    row, _ = await run_case("E03", group, tmp_path, fault=fault)
    assert row["status"] == "incomplete"
    expected = 0 if fault == "duplicate_id" else 2
    assert row["metrics"]["handler_executions"] == expected
    assert row["metrics"]["tool_attempts"] == expected
    assert row["metrics"]["by_stage"].get("acceptance", 0) == 0
    assert row["independent_score"]["grade"] is None
    if fault == "classification_only":
        assert row["result"]["evidence_validation"]["complete"] is False
    if fault == "duplicate_id":
        events = read_trace(tmp_path / row["trace_path"])
        assert any(e["payload"].get("worker_status") == "protocol_error" for e in events)
    if fault == "replay_after_retry":
        assert row["metrics"]["failed_requests"] == 1
        assert row["metrics"]["by_stage"]["worker"] == 3
        assert len(row["retrievals"]) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("group", ["A", "B", "C"])
async def test_rag_off_uses_real_tool_failure_and_never_completes(tmp_path, group):
    row, _ = await run_case("E03", group, tmp_path, rag_on=False)
    assert row["status"] == "incomplete"
    assert row["metrics"]["handler_executions"] == 2
    assert all(t["status"] == "failed" for t in row["tools"])
    assert row["result"]["evidence_validation"]["complete"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "limits,key,maximum",
    [
        ({"worker_limit": 2}, "worker", 2),
        ({"model_limit": 2}, "all", 2),
    ],
)
async def test_real_native_concurrent_budget_exhaustion_stops_attempts(
    tmp_path, limits, key, maximum
):
    row, _ = await run_case("D03", "C", tmp_path, limits=limits)
    assert row["status"] == "budget_exhausted" and row["native_status"] != "completed"
    used = row["metrics"]["model_attempts"] if key == "all" else row["metrics"]["by_stage"][key]
    assert used == maximum
    assert row["metrics"]["handler_executions"] == 0
    events = read_trace(tmp_path / row["trace_path"])
    assert events[-1]["event_type"] == "run_end"
    assert events[-1]["payload"]["status"] == "budget_exhausted"


@pytest.mark.asyncio
@pytest.mark.parametrize("group", ["A", "B", "C"])
async def test_real_tool_budget_stops_second_handler_and_model_continuation(tmp_path, group):
    row, _ = await run_case("E03", group, tmp_path, limits={"tool_limit": 1})
    assert row["status"] == "budget_exhausted" and row["native_status"] != "completed"
    assert row["metrics"]["handler_executions"] == row["metrics"]["tool_attempts"] == 1
    assert row["metrics"]["by_stage"]["worker"] == 1


@pytest.mark.asyncio
async def test_atomic_default_budget_reserves_exact_real_provider_attempts():
    llm = ScriptedLLM([LLMResponse("ok")] * 30)
    budget = RunBudget()
    client = MeteredLLM(llm, budget)
    messages = [{"role": "system", "content": "Role: General"}, {"role": "user", "content": "{}"}]
    outcomes = await asyncio.gather(
        *(client.complete(messages) for _ in range(30)), return_exceptions=True
    )
    assert len(llm.requests) == 8
    assert sum(isinstance(o, RunBudgetExceeded) for o in outcomes) == 22
    assert budget.summary()["model_attempts"] == 8
    assert budget.summary()["input_tokens"] == "UNKNOWN"


@pytest.mark.asyncio
async def test_all_model_limit_includes_validation_and_nonworker_attempts():
    llm = ScriptedLLM([LLMResponse("ok")] * 25)
    budget = RunBudget()
    primary = MeteredLLM(llm, budget)
    validator = MeteredLLM(llm, budget, validation=True)
    planning = [{"role": "system", "content": "You are the public MedAgent centralized planner"}]
    await asyncio.gather(*(primary.complete(planning) for _ in range(15)))
    await validator.complete([])
    with pytest.raises(RunBudgetExceeded):
        await validator.complete([])
    assert len(llm.requests) == 16
    assert budget.summary()["by_stage"] == {"planning": 15, "acceptance": 1}


@pytest.mark.asyncio
async def test_tool_attempts_and_real_handlers_count_permission_schema_and_exception():
    registry = ToolRegistry(max_calls=16)
    actual = []

    def handler(**kwargs):
        actual.append(kwargs)
        raise RuntimeError("synthetic side effect then error")

    registry.register("search_knowledge", handler)
    budget = RunBudget()
    tools = MeteredTools(registry, budget)
    with pytest.raises(PermissionError):
        await tools.execute("no_such_role", "search_knowledge", {"query": "cough"})
    with pytest.raises(ToolArgumentError):
        await tools.execute("research_agent", "search_knowledge", {"bad": "field"})
    for _ in range(6):
        with pytest.raises(RuntimeError):
            await tools.execute("research_agent", "search_knowledge", {"query": "cough"})
    with pytest.raises(RunBudgetExceeded):
        await tools.execute("research_agent", "search_knowledge", {"query": "cough"})
    assert len(actual) == 6 and budget.summary()["handler_executions"] == 6
    assert budget.summary()["tool_attempts"] == 8


@pytest.mark.asyncio
async def test_usage_unknown_and_scripted_usage_are_not_zero_or_provider_measured():
    class Fails(ScriptedLLM):
        async def complete(self, messages, **kwargs):
            raise httpx.ConnectError("offline")

    budget = RunBudget()
    client = MeteredLLM(Fails([]), budget, validation=True)
    with pytest.raises(httpx.ConnectError):
        await client.complete([])
    client = MeteredLLM(
        ScriptedLLM([LLMResponse("x", usage={"prompt_tokens": 12, "completion_tokens": 3})]),
        budget,
        validation=True,
        usage_origin="scripted_simulation",
    )
    await client.complete([])
    assert budget.models[0]["input_tokens"] == "UNKNOWN"
    assert budget.models[1]["input_tokens"] == 12
    assert budget.models[1]["usage_origin"] == "scripted_simulation"
    assert budget.summary()["input_tokens"] == "UNKNOWN"
    assert budget.summary()["failed_requests"] == 1


@pytest.mark.asyncio
async def test_serial_replay_rejects_changed_memory_and_rag_configuration(tmp_path):
    original, plan = await run_case("M01", "C", tmp_path / "C")
    changed, _ = await run_case(
        "M01", "C-serial", tmp_path / "serial", replay=plan, memory_on=False
    )
    assert original["status"] == "completed" and changed["status"] == "error"
    assert changed["metrics"]["model_attempts"] == 0
    assert changed["metrics"]["handler_executions"] == 0


def test_frozen_schema_and_data_remain_exactly_intact_and_scoring_requires_human():
    import hashlib

    manifest = json.loads((ROOT / "freeze/manifest.json").read_text(encoding="utf-8"))
    # Public subset omits the two archived originals; do not claim their verification.
    import subprocess

    result = subprocess.run(
        ["python", str(ROOT.parent / "public_release/validate_public_subset.py")],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    report = json.loads(result.stdout)
    assert report["status"] == "PASS_PUBLIC_SUBSET_ONLY"
    assert report["source_document_verification"] == "NOT_PERFORMED"
    assert report["public_files"] == 18 and report["archived_originals"] == 2
    assert report["mutations_rejected"] == 8
    assert hashlib.sha256((ROOT / "cases.json").read_bytes()).hexdigest() == (
        "be12cecebc20250415874f616a955353b06c8cfc391e9dc62514be130f097305"
    )
    assert manifest["frozen"] is True
    assert independent_score(load_case("S02"), {"status": "completed"})["grade"] is None
    with pytest.raises(ValueError):
        independent_score(load_case("S02"), {"status": "completed"}, reviewer="x", grade="PASS")
    assert TOOL_SCHEMAS["disease_code"]["function"]["name"] == "disease_code"
    assert Path(ROOT).name == "frozen_v1"


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["protocol_repair", "length_recovery"])
@pytest.mark.parametrize("group", ["A", "C"])
async def test_recovery_real_model_attempts_count_against_shared_worker_budget(
    tmp_path, group, fault
):
    row, _ = await run_case("E03", group, tmp_path / "normal", fault=fault)
    assert row["status"] == "completed"
    assert row["metrics"]["by_stage"]["worker"] == 3
    assert row["metrics"]["handler_executions"] == 2
    assert row["recovery_events"]
    limited, _ = await run_case(
        "E03", group, tmp_path / "limited", fault=fault, limits={"worker_limit": 2}
    )
    assert limited["status"] == "budget_exhausted"
    assert limited["metrics"]["by_stage"]["worker"] == 2
    assert limited["metrics"]["handler_executions"] == 2


@pytest.mark.asyncio
async def test_serial_real_native_replay_never_invokes_planner_or_policy(tmp_path, monkeypatch):
    owner, plan = await run_case("D03", "C", tmp_path / "owner")

    def forbidden(*args, **kwargs):
        raise AssertionError("serial attempted to replan")

    monkeypatch.setattr("medagent.runtime.native_engine.Planner.plan", forbidden)
    monkeypatch.setattr("medagent.runtime.native_engine.apply_contract_policy", forbidden)
    serial, replay = await run_case(
        "D03", "C-serial", tmp_path / "serial", replay=plan, shared_plan_cost=shared_cost(owner)
    )
    assert serial["status"] == "completed" and replay == plan
    assert serial["metrics"]["by_stage"].get("planning", 0) == 0
    assert (
        serial["run_cost_views"]["full_architecture_attributed_model_attempts"]
        == (owner["run_cost_views"]["full_architecture_attributed_model_attempts"])
    )
