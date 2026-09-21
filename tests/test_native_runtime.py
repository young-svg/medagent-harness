from __future__ import annotations

import asyncio
import json
from functools import wraps
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.main import app
from medagent.context.contract import AnswerContract, build_answer_contract
from medagent.context.evidence_ledger import EvidenceLedger, build_evidence_ledger
from medagent.llm import LLMResponse, ScriptedLLM, ToolCall
from medagent.memory.session import SessionMemory
from medagent.observability.replay import read_trace, replay
from medagent.planning.planner import Planner
from medagent.planning.router import Router
from medagent.retrieval.backend import FakeRetrievalBackend
from medagent.retrieval.evidence import EvidenceItem
from medagent.runtime.config import RuntimeConfig
from medagent.runtime.native_engine import NativeMedAgentEngine
from medagent.tools.registry import ToolRegistry


def async_test(function: Any) -> Any:
    @wraps(function)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return asyncio.run(function(*args, **kwargs))

    return wrapper


def test_contract_and_ledger_round_trip() -> None:
    contract = build_answer_contract("Give the diagnosis, differential, and treatment plan")
    assert contract.requested_deliverables == [
        "TREATMENT_PLAN",
        "DIFFERENTIAL_DIAGNOSIS",
        "DIAGNOSIS_WITH_BASIS",
    ]
    assert AnswerContract.from_dict(contract.to_dict()).intent == "multi_deliverable"
    ledger = build_evidence_ledger("fever; no rash")
    assert EvidenceLedger.from_dict(ledger.to_dict()).findings[1].source_text == "no rash"


@pytest.mark.parametrize(
    ("question", "expects_further_tests"),
    [
        ("请分析上述辅助检查结果", False),
        ("下一步还需要完善哪些检查？", True),
        ("分析现有检查，并说明还需要进一步做哪些检查", True),
    ],
)
def test_contract_detects_only_prospective_further_tests(
    question: str, expects_further_tests: bool
) -> None:
    contract = build_answer_contract(question)

    assert ("FURTHER_TESTS" in contract.requested_deliverables) is expects_further_tests


@async_test
async def test_planner_parse_missing_agent_and_fallback() -> None:
    contract = build_answer_contract("diagnosis")
    ledger = build_evidence_ledger("fact")
    llm = ScriptedLLM([{"subtasks": [{"description": "assess"}]}])
    plan = await Planner(llm).plan("diagnosis", contract, ledger)
    assert plan.subtasks[0].assigned_agent == "diagnostic_agent"
    assert Router().route(plan).mode == "single"
    fallback = Planner.parse("not-json", "diagnostic_agent")
    assert fallback.fallback_reason == "structured_parse_fallback:JSONDecodeError"


@async_test
async def test_tool_visibility_budget_and_execution_denial() -> None:
    registry = ToolRegistry(max_calls=2)
    registry.register("assess_risk", lambda symptoms: symptoms)
    names = {item["function"]["name"] for item in registry.schemas_for("diagnostic_agent")}
    assert names == {"assess_risk"}
    assert await registry.execute("diagnostic_agent", "assess_risk", {"symptoms": "one"})
    assert await registry.execute("diagnostic_agent", "assess_risk", {"symptoms": "two"})
    with pytest.raises(RuntimeError, match="budget"):
        await registry.execute("diagnostic_agent", "assess_risk", {"symptoms": "three"})
    with pytest.raises(PermissionError):
        await registry.execute("research_agent", "assess_risk", {"symptoms": "x"})


@async_test
async def test_native_single_route_end_to_end(tmp_path) -> None:
    llm = ScriptedLLM(
        [
            {
                "subtasks": [
                    {
                        "subtask_id": "t1",
                        "description": "Assess",
                        "assigned_agent": "diagnostic_agent",
                    }
                ]
            },
            "Diagnosis with basis\n\nA cautious worker draft.",
        ]
    )
    engine = NativeMedAgentEngine(RuntimeConfig(trace_dir=str(tmp_path)), llm=llm)
    result = await engine.analyze("Synthetic fatigue", "What is the diagnosis?", "single")
    await engine.close()
    assert result["final_answer"] == result["presentation"]["professional_answer"]
    assert result["presentation"]["execution_summary"]["route"]["mode"] == "single"
    events = read_trace(tmp_path / result["run_id"])
    assert events[0]["event_type"] == "run_start"
    assert events[-1]["event_type"] == "run_end"
    assert replay(tmp_path / result["run_id"])["final_answer"] == result["final_answer"]


@async_test
async def test_native_multi_tool_rag_topology_and_evidence_cards(tmp_path) -> None:
    llm = ScriptedLLM(
        [
            {
                "subtasks": [
                    {
                        "subtask_id": "t1",
                        "description": "Assess",
                        "assigned_agent": "diagnostic_agent",
                    },
                    {
                        "subtask_id": "t2",
                        "description": "Research",
                        "assigned_agent": "research_agent",
                    },
                ]
            },
            LLMResponse(tool_calls=[ToolCall("c1", "search_knowledge", {"query": "assessment"})]),
            "Diagnostic draft",
            "Research draft",
            "Synthesis output",
        ]
    )
    backend = FakeRetrievalBackend(
        [
            EvidenceItem(
                "ev-1",
                "doc-1",
                "source-1",
                "Reference",
                "Assessment",
                "Journal",
                None,
                "Admitted evidence text",
                0.91,
                1,
            )
        ]
    )
    engine = NativeMedAgentEngine(
        RuntimeConfig(trace_dir=str(tmp_path)), llm=llm, retrieval_backend=backend
    )
    result = await engine.analyze("Synthetic symptom", "diagnosis with research evidence", "multi")
    events = read_trace(tmp_path / result["run_id"])
    by_id = {item["event_id"]: item for item in events}
    query = next(item for item in events if item["event_type"] == "retrieval_query")
    retrieval = next(item for item in events if item["event_type"] == "retrieval_result")
    assert by_id[query["parent_event_id"]]["event_type"] == "tool_call"
    assert retrieval["parent_event_id"] == query["event_id"]
    assert result["presentation"]["evidence_cards"][0]["evidence_id"] == "ev-1"
    assert result["presentation"]["professional_answer"] == result["final_answer"]
    await engine.close()


@async_test
async def test_multi_turn_memory_current_input_priority(tmp_path) -> None:
    memory = SessionMemory(recent_limit=4)
    responses = [
        {"subtasks": [{"description": "first", "assigned_agent": "diagnostic_agent"}]},
        "First answer",
        {"subtasks": [{"description": "second", "assigned_agent": "diagnostic_agent"}]},
        "Corrected answer",
    ]
    engine = NativeMedAgentEngine(
        RuntimeConfig(trace_dir=str(tmp_path)), llm=ScriptedLLM(responses), memory=memory
    )
    await engine.analyze("A", "Assess A", "same")
    await engine.analyze("Correction A-prime", "Reassess", "same")
    context = memory.context("same", "Correction A-prime")
    assert context[-1]["content"] == "Correction A-prime"
    assert any("A-prime" in item["content"] for item in context)
    assert memory.context("other", "B") == [{"role": "user", "content": "B"}]
    await engine.close()


def test_api_health_and_native_analysis(tmp_path, monkeypatch) -> None:
    from api import main

    engine = NativeMedAgentEngine(RuntimeConfig(trace_dir=str(tmp_path)))
    monkeypatch.setattr(main, "_coordinator", engine)
    with TestClient(app) as client:
        assert client.get("/health").json()["mode"] == "native"
        response = client.post(
            "/api/analyze",
            json={
                "description": "Synthetic fatigue",
                "question": "What should be assessed?",
                "session_id": "api-test",
            },
        )
    assert response.status_code == 200
    body = response.json()
    assert body["final_answer"] == body["presentation"]["professional_answer"]


def test_trace_schema_is_public_and_complete(tmp_path) -> None:
    required = {
        "event_id",
        "parent_event_id",
        "run_id",
        "timestamp",
        "stage",
        "event_type",
        "agent",
        "payload",
    }
    event = json.loads((tmp_path / "missing").as_posix()) if False else None
    assert event is None
    from medagent.observability.schema import TraceEvent

    assert required == set(TraceEvent("r", "stage", "run_start").to_dict())


@async_test
async def test_single_route_bypasses_synthesis(tmp_path) -> None:
    llm = ScriptedLLM(
        [
            {
                "subtasks": [
                    {
                        "subtask_id": "single-1",
                        "description": "Assess the synthetic case.",
                        "assigned_agent": "diagnostic_agent",
                    }
                ]
            },
            "VALID_WORKER_ANSWER",
            "",
        ]
    )
    engine = NativeMedAgentEngine(RuntimeConfig(trace_dir=str(tmp_path)), llm=llm)

    result = await engine.analyze("Synthetic case", "Assess safely", "single-bypass")
    events = read_trace(tmp_path / result["run_id"])

    assert len(llm.requests) == 2
    assert result["final_answer"] == "VALID_WORKER_ANSWER"
    assert not any(event["event_type"].startswith("synthesis_") for event in events)
    assert events[-1]["event_type"] == "run_end"
    assert events[-1]["payload"]["status"] == "completed"
    await engine.close()


@async_test
async def test_single_successful_worker_bypasses_synthesis(tmp_path) -> None:
    llm = ScriptedLLM(
        [
            {
                "subtasks": [
                    {
                        "subtask_id": "worker-1",
                        "description": "Produce the valid draft.",
                        "assigned_agent": "diagnostic_agent",
                    },
                    {
                        "subtask_id": "worker-2",
                        "description": "This worker returns no usable draft.",
                        "assigned_agent": "research_agent",
                    },
                ]
            },
            "VALID_WORKER_ANSWER",
            "",
            "",
        ]
    )
    engine = NativeMedAgentEngine(RuntimeConfig(trace_dir=str(tmp_path)), llm=llm)

    result = await engine.analyze(
        "Synthetic case", "Assess with optional research", "one-successful-worker"
    )
    events = read_trace(tmp_path / result["run_id"])

    assert len(llm.requests) == 3
    assert result["final_answer"] == "VALID_WORKER_ANSWER"
    assert not any(event["event_type"].startswith("synthesis_") for event in events)
    assert events[-1]["payload"]["status"] == "completed"
    await engine.close()


@async_test
async def test_multi_route_still_uses_synthesis(tmp_path) -> None:
    llm = ScriptedLLM(
        [
            {
                "subtasks": [
                    {
                        "subtask_id": "multi-1",
                        "description": "Assess.",
                        "assigned_agent": "diagnostic_agent",
                    },
                    {
                        "subtask_id": "multi-2",
                        "description": "Research.",
                        "assigned_agent": "research_agent",
                    },
                ]
            },
            "Diagnostic draft",
            "Research draft",
            "VALID_SYNTHESIS_ANSWER",
        ]
    )
    engine = NativeMedAgentEngine(RuntimeConfig(trace_dir=str(tmp_path)), llm=llm)

    result = await engine.analyze("Synthetic case", "Assess and research", "multi-synthesis")
    events = read_trace(tmp_path / result["run_id"])

    assert len(llm.requests) == 4
    assert result["final_answer"] == "VALID_SYNTHESIS_ANSWER"
    assert any(event["event_type"] == "synthesis_input" for event in events)
    assert any(event["event_type"] == "synthesis_output" for event in events)
    await engine.close()


@async_test
async def test_admitted_evidence_appears_once_in_next_worker_request(tmp_path) -> None:
    llm = ScriptedLLM(
        [
            {
                "subtasks": [
                    {
                        "subtask_id": "rag-1",
                        "description": "Retrieve the synthetic evidence.",
                        "assigned_agent": "research_agent",
                    }
                ]
            },
            LLMResponse(
                tool_calls=[
                    ToolCall("call-1", "clinical_guideline", {"query": "guideline"}),
                    ToolCall("call-2", "clinical_guideline", {"query": "guideline"}),
                ]
            ),
            "Evidence-aware worker answer",
        ]
    )
    items = [
        EvidenceItem(
            f"evidence-{index}",
            f"document-{index}",
            f"source-{index}",
            f"Title {index}",
            "Guideline",
            "Synthetic source",
            None,
            f"UNIQUE_EVIDENCE_TEXT_{index}",
            0.9 - index / 100,
            index,
        )
        for index in range(1, 6)
    ]
    engine = NativeMedAgentEngine(
        RuntimeConfig(trace_dir=str(tmp_path), retrieval_threshold=0.63),
        llm=llm,
        retrieval_backend=FakeRetrievalBackend(items),
    )

    result = await engine.analyze(
        "Synthetic retrieval case", "Retrieve a clinical guideline", "evidence-once"
    )
    worker_messages = llm.requests[2]["messages"]
    next_worker_request = json.dumps(worker_messages, ensure_ascii=False)
    events = read_trace(tmp_path / result["run_id"])

    for item in items:
        assert next_worker_request.count(item.evidence_id) == 1
        assert next_worker_request.count(item.text) == 1
    tool_payloads = [
        json.loads(str(message["content"]))
        for message in worker_messages
        if message["role"] == "tool"
    ]
    assert all(
        set(payload) == {"query", "evidence_status", "admitted"}
        for payload in tool_payloads
    )
    assert len(tool_payloads[0]["admitted"]) == 5
    assert tool_payloads[0]["evidence_status"] == "relevant_evidence_admitted"
    assert tool_payloads[1]["admitted"] == []
    assert tool_payloads[1]["evidence_status"] == "no_new_relevant_evidence"
    retrievals = [event for event in events if event["event_type"] == "retrieval_result"]
    assert len(retrievals) == 2
    assert all(len(event["payload"]["raw_candidates"]) == 5 for event in retrievals)
    await engine.close()
