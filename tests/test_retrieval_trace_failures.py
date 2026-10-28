"""Offline regressions for retrieval status and failed-query trace causality."""

from __future__ import annotations

import pytest

from medagent.llm import LLMResponse, ScriptedLLM, ToolCall
from medagent.observability.replay import read_trace
from medagent.retrieval.backend import FakeRetrievalBackend, RetrievalSchemaError
from medagent.retrieval.evidence import EvidenceItem
from medagent.runtime.config import RuntimeConfig
from medagent.runtime.native_engine import NativeMedAgentEngine


def _evidence(score: float) -> EvidenceItem:
    return EvidenceItem(
        evidence_id="synthetic-1",
        document_id="synthetic-document",
        source_id="synthetic-source",
        title="Synthetic fixture",
        section="test",
        source="offline-test",
        url=None,
        text="Synthetic evidence only.",
        score=score,
        rank=1,
    )


async def _run(tmp_path, backend, *, tool_calls: int = 1):
    responses = [
        {
            "subtasks": [
                {
                    "subtask_id": "ST1",
                    "description": "Inspect synthetic guideline evidence.",
                    "assigned_agent": "research_agent",
                    "request_item_ids": ["RQ1"],
                }
            ]
        },
        *[
            LLMResponse(
                tool_calls=[
                    ToolCall(f"call-{index}", "clinical_guideline", {"query": "synthetic"})
                ]
            )
            for index in range(tool_calls)
        ],
        {"answers": [{"request_item_id": "RQ1", "answer": "Synthetic result only."}]},
    ]
    llm = ScriptedLLM(responses)
    engine = NativeMedAgentEngine(
        RuntimeConfig(trace_dir=str(tmp_path), max_tool_calls=tool_calls),
        llm=llm,
        retrieval_backend=backend,
    )
    try:
        result = await engine.analyze(
            "Synthetic fixture only.",
            "Use a guideline to inspect synthetic evidence.",
            "retrieval-trace-test",
        )
    finally:
        await engine.close()
    return result, read_trace(tmp_path / result["run_id"]), llm


@pytest.mark.asyncio
async def test_first_empty_admission_is_no_relevant_evidence(tmp_path) -> None:
    backend = FakeRetrievalBackend([_evidence(0.62)])
    result, events, llm = await _run(tmp_path, backend)

    tool_result = next(event for event in events if event["event_type"] == "tool_result")
    retrieval_result = next(
        event for event in events if event["event_type"] == "retrieval_result"
    )
    assert tool_result["payload"]["result"]["evidence_status"] == "no_relevant_evidence"
    assert tool_result["payload"]["result"]["admitted"] == []
    assert retrieval_result["payload"]["admission"][0]["admitted"] is False
    assert result["status"] == "completed"
    assert len(llm.requests) == 3


@pytest.mark.asyncio
async def test_repeated_admitted_evidence_is_no_new_relevant_evidence(tmp_path) -> None:
    backend = FakeRetrievalBackend([_evidence(0.92)])
    result, events, llm = await _run(tmp_path, backend, tool_calls=2)

    tool_results = [event for event in events if event["event_type"] == "tool_result"]
    assert [event["payload"]["result"]["evidence_status"] for event in tool_results] == [
        "relevant_evidence_admitted",
        "no_new_relevant_evidence",
    ]
    assert len(tool_results[0]["payload"]["result"]["admitted"]) == 1
    assert tool_results[1]["payload"]["result"]["admitted"] == []
    assert len([event for event in events if event["event_type"] == "retrieval_query"]) == 2
    assert len([event for event in events if event["event_type"] == "retrieval_result"]) == 2
    assert result["status"] == "completed"
    assert len(llm.requests) == 4


@pytest.mark.asyncio
async def test_failed_retrieval_preserves_query_and_error_causality(tmp_path) -> None:
    class FailingBackend:
        request: tuple[str, str, int] | None = None

        async def search(self, query: str, collection: str, top_k: int):
            self.request = (query, collection, top_k)
            raise RetrievalSchemaError("synthetic collection unavailable")

    backend = FailingBackend()
    result, events, llm = await _run(tmp_path, backend)
    by_id = {event["event_id"]: event for event in events}
    call = next(event for event in events if event["event_type"] == "tool_call")
    query = next(event for event in events if event["event_type"] == "retrieval_query")
    error = next(
        event
        for event in events
        if event["event_type"] == "error" and event["stage"] == "retrieval"
    )
    tool_result = next(event for event in events if event["event_type"] == "tool_result")

    assert backend.request is not None
    assert query["payload"]["query"] == backend.request[0]
    assert query["payload"]["collection"] == backend.request[1]
    assert query["payload"]["top_k"] == backend.request[2]
    assert query["payload"]["subtask_id"] == "ST1"
    assert query["parent_event_id"] == call["event_id"]
    assert error["parent_event_id"] == query["event_id"]
    assert error["payload"] == {
        "where": "retrieval",
        "tool": "clinical_guideline",
        "error_type": "RetrievalSchemaError",
    }
    assert tool_result["parent_event_id"] == error["event_id"]
    assert tool_result["payload"]["result"] == {
        "error": "synthetic collection unavailable"
    }
    assert by_id[call["parent_event_id"]]["event_type"] == "llm_response"
    assert not any(event["event_type"] == "retrieval_result" for event in events)
    assert result["status"] == "completed"  # A valid abstaining answer may still complete.
    assert len(llm.requests) == 3
