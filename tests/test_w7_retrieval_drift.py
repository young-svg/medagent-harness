"""Frozen evaluation must not consume a corpus changed after condition capture."""
from __future__ import annotations

import json

import pytest

from medagent.llm import LLMResponse, ScriptedLLM, ToolCall
from medagent.memory.session import SessionMemory
from medagent.observability.replay import read_trace
from medagent.retrieval.backend import FakeRetrievalBackend
from medagent.retrieval.query_builder import QueryBuilder
from medagent.runtime.config import RuntimeConfig
from medagent.runtime.evaluation_guard import EvaluationConditionDriftError
from medagent.runtime.native_engine import NativeMedAgentEngine
from tests.test_semantic_validation import verdict
from tests.test_w5_evidence_closure import source
from tests.test_w7_evaluation_interfaces import frozen

QUESTION = "Consult external materials and identify the sources."
ANSWER = "SYNTHETIC example only [EVIDENCE:ev-1]"


def corpus():
    return FakeRetrievalBackend([source(metadata={"catalogue": {"tags": ["SYNTHETIC"]}})])


def responses(*, planner=True):
    plan = {"subtasks": [{"subtask_id": "lookup", "assigned_agent": "research_agent",
                         "description": QUESTION, "request_item_ids": ["RQ1"],
                         "deliverable_ids": ["COMPREHENSIVE_CASE_ANALYSIS"]}]}
    return ([plan] if planner else []) + [
        LLMResponse(tool_calls=[ToolCall("lookup-1", "search_knowledge",
                                        {"query": "SYNTHETIC"})]),
        {"answers": [{"request_item_id": "RQ1", "answer": ANSWER}]},
    ]


class MutatingPlanner(ScriptedLLM):
    mutate = None

    async def complete(self, messages, **kwargs):
        response = await super().complete(messages, **kwargs)
        if len(self.requests) == 1 and self.mutate is not None:
            self.mutate()
        return response


def engine_at(path, backend, llm, *, serial=False):
    return NativeMedAgentEngine(
        RuntimeConfig(trace_dir=str(path), max_concurrent_workers=1 if serial else 4),
        llm=llm, validation_llm=ScriptedLLM([verdict([ANSWER])]),
        retrieval_backend=backend, memory=SessionMemory(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["list", "item", "nested", "backend", "model", "memory"])
async def test_planner_drift_aborts_before_worker_or_retrieval(tmp_path, change):
    backend, replacement = corpus(), corpus()
    llm = MutatingPlanner(responses())
    engine = engine_at(tmp_path, backend, llm)

    def mutate():
        if change == "list":
            backend.items.append(source("private-added", text="PRIVATE_CORPUS_CONTENT"))
        elif change == "item":
            backend.items[0].text = "PRIVATE_CORPUS_CONTENT"
        elif change == "nested":
            backend.items[0].metadata["catalogue"]["tags"].append("PRIVATE_CORPUS_CONTENT")
        elif change == "backend":
            engine.retrieval_backend = replacement
        elif change == "model":
            llm.temperature = 0.7
        else:
            engine.memory.enabled = False

    llm.mutate = mutate
    reason = {"model": "model_parameters_drift", "memory": "memory_configuration_drift"}.get(
        change, "retrieval_conditions_drift",
    )
    try:
        with pytest.raises(EvaluationConditionDriftError, match=reason):
            await engine.analyze_frozen(frozen(QUESTION))
        assert backend.requests == replacement.requests == []
        assert len(llm.requests) == 1
        assert engine.validation_llm.requests == []
        events = read_trace(next(tmp_path.iterdir()))
        assert events[-1]["payload"]["status"] == "failed"
        error = next(e["payload"] for e in events if e["event_type"] == "error")
        assert error["failure_reason"] == reason and error["failure_stage"] == "evaluation"
        assert not any(e["event_type"] == "tool_call" for e in events)
        assert "PRIVATE_CORPUS_CONTENT" not in json.dumps(events)
    finally:
        await engine.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("when", ["before_search", "during_search"])
async def test_actual_retrieval_boundary_rejects_late_corpus_drift(tmp_path, monkeypatch, when):
    class DuringSearch(FakeRetrievalBackend):
        async def search(self, *args):
            result = await super().search(*args)
            self.items[0].metadata["catalogue"]["tags"].append("PRIVATE_CORPUS_CONTENT")
            return result

    backend = corpus()
    if when == "during_search":
        backend = DuringSearch(backend.items)
    else:
        original = QueryBuilder.build

        def mutate_before_search(builder, *args, **kwargs):
            result = original(builder, *args, **kwargs)
            backend.items[0].text = "PRIVATE_CORPUS_CONTENT"
            return result

        monkeypatch.setattr(QueryBuilder, "build", mutate_before_search)
    llm = ScriptedLLM(responses())
    engine = engine_at(tmp_path, backend, llm)
    try:
        with pytest.raises(EvaluationConditionDriftError, match="retrieval_conditions_drift"):
            await engine.analyze_frozen(frozen(QUESTION))
        assert len(backend.requests) == (1 if when == "during_search" else 0)
        assert len(llm.requests) == 2  # Planner and tool request; no model continuation.
        assert engine.validation_llm.requests == []
        events = read_trace(next(tmp_path.iterdir()))
        assert events[-1]["payload"]["status"] == "failed"
        assert any(e["event_type"] == "tool_call" for e in events)
        error = next(e["payload"] for e in events if e["event_type"] == "error")
        assert error["failure_reason"] == "retrieval_conditions_drift"
        assert "PRIVATE_CORPUS_CONTENT" not in json.dumps(events)
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_unchanged_corpus_completes_and_replay_requires_identical_content(tmp_path):
    request = frozen(QUESTION)
    backend = corpus()
    engine = engine_at(tmp_path / "c", backend, ScriptedLLM(responses()))
    try:
        captured = await engine.analyze_frozen(request)
        assert captured.result["status"] == "completed"
        assert len(backend.requests) == 1
        assert backend.items[0].admitted is False  # Admission only mutates result snapshots.
        events = read_trace(tmp_path / "c" / captured.result["run_id"])
        assert events[-1]["payload"]["status"] == "completed"
        assert captured.result["evidence_validation"]["complete"] is True
    finally:
        await engine.close()

    for changed in (False, True):
        backend = corpus()
        if changed:
            backend.items[0].metadata["catalogue"]["tags"].append("PRIVATE_CORPUS_CONTENT")
        llm = ScriptedLLM(responses(planner=False))
        path = tmp_path / ("different" if changed else "identical")
        engine = engine_at(path, backend, llm, serial=True)
        try:
            if changed:
                with pytest.raises(ValueError, match="conditions mismatch"):
                    await engine.analyze_frozen(request, plan=captured.plan)
                assert backend.requests == [] and llm.requests == []
                events = read_trace(next(path.iterdir()))
                assert events[-1]["payload"]["status"] == "failed"
                assert "PRIVATE_CORPUS_CONTENT" not in json.dumps(events)
            else:
                replayed = await engine.analyze_frozen(request, plan=captured.plan)
                assert replayed.result["status"] == "completed"
                assert len(backend.requests) == 1 and len(llm.requests) == 2
                assert captured.plan == replayed.plan
        finally:
            await engine.close()
