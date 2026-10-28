"""B1 regression tests for trusted, per-subtask retrieval context."""

import asyncio
import json
from copy import deepcopy
from unittest.mock import Mock

import pytest

from medagent.agents.diagnostic import DIAGNOSTIC_AGENT
from medagent.context.contract import build_answer_contract
from medagent.context.evidence_ledger import EvidenceLedger
from medagent.context.request_spec import build_request_spec
from medagent.llm import LLMResponse, ScriptedLLM, ToolCall
from medagent.observability.replay import read_trace, replay
from medagent.observability.tracer import TraceRecorder
from medagent.planning.complexity import build_complexity_profile, build_response_profile
from medagent.planning.models import Subtask
from medagent.retrieval.backend import FakeRetrievalBackend
from medagent.retrieval.evidence import EvidenceBundle
from medagent.retrieval.query_builder import QueryBuilder
from medagent.runtime.agent_loop import AgentLoop
from medagent.runtime.config import RuntimeConfig
from medagent.runtime.native_engine import NativeMedAgentEngine
from medagent.skills.loader import load_public_skills
from medagent.tools.registry import ToolExecutionContext, ToolRegistry
from medagent.tools.schemas import TOOL_SCHEMAS


@pytest.mark.parametrize("distinct_tool_inputs", [False, True])
def test_engine_queries_use_current_subtask_and_deliverables(
    tmp_path, monkeypatch, distinct_tool_inputs
):
    question = "Using guidelines, provide the diagnosis and treatment plan."
    descriptions = {
        "diagnostic_agent": "Assess diagnostic criteria and competing causes.",
        "consultation_agent": "Review medication options and safety precautions.",
    }
    inputs = {
        "diagnostic_agent": "synthetic condition diagnostic criteria"
        if distinct_tool_inputs
        else "synthetic condition evidence",
        "consultation_agent": "synthetic condition medication options"
        if distinct_tool_inputs
        else "synthetic condition evidence",
    }
    backend = FakeRetrievalBackend()
    builder_calls = []
    original_build = QueryBuilder.build

    def capture_build(self, question, subtask, deliverable, tool_input="", **kwargs):
        builder_calls.append((question, subtask, deliverable, tool_input, kwargs))
        return original_build(self, question, subtask, deliverable, tool_input, **kwargs)

    monkeypatch.setattr(QueryBuilder, "build", capture_build)

    class TaskAwareOfflineLLM(ScriptedLLM):
        def __init__(self):
            super().__init__(
                [
                    {
                        "subtasks": [
                            {
                                "subtask_id": agent,
                                "description": description,
                                "assigned_agent": agent,
                                "deliverable_ids": [
                                    "DIAGNOSIS_WITH_BASIS"
                                    if agent == "diagnostic_agent"
                                    else "TREATMENT_PLAN"
                                ],
                            }
                            for agent, description in descriptions.items()
                        ]
                    },
                    {"answers": []},  # A2 deterministically preserves Worker answers.
                ]
            )
            self.worker_contexts = {}
            self.worker_calls = {}

        async def complete(self, messages, **kwargs):
            if not messages[0]["content"].startswith("Role:"):
                return await super().complete(messages, **kwargs)
            context = json.loads(messages[1]["content"])
            task = context["subtask"]
            worker = task["assigned_agent"]
            self.worker_contexts[worker] = context
            self.worker_calls[worker] = self.worker_calls.get(worker, 0) + 1
            if self.worker_calls[worker] == 1:
                return LLMResponse(
                    tool_calls=[ToolCall(worker, "clinical_guideline", {"query": inputs[worker]})]
                )
            return LLMResponse(
                content=json.dumps(
                    {
                        "answers": [
                            {"request_item_id": item, "answer": f"A cautious {worker} response."}
                            for item in task["request_item_ids"]
                        ]
                    }
                )
            )

    llm = TaskAwareOfflineLLM()
    config = RuntimeConfig(trace_dir=str(tmp_path), max_tool_calls=1)
    engine = NativeMedAgentEngine(config, llm=llm, retrieval_backend=backend)

    async def run():
        try:
            return await engine.analyze("Synthetic condition only.", question, "b1-audit")
        finally:
            await engine.close()

    result = asyncio.run(run())
    events = read_trace(tmp_path / result["run_id"])
    assert set(llm.worker_contexts) == set(descriptions)
    assert llm.worker_calls == {worker: 2 for worker in descriptions}
    assert len(llm.requests) == 2  # Planner and Synthesizer only; Workers counted above.
    assert result["trace"]["llm_calls"] == 6
    assert len(backend.requests) == len(builder_calls) == 2
    queries = [event for event in events if event["event_type"] == "retrieval_query"]
    assert len(queries) == 2
    by_id = {event["event_id"]: event for event in events}
    observed = []
    for query_event, backend_request, build_call in zip(
        queries, backend.requests, builder_calls, strict=True
    ):
        worker = query_event["agent"]
        context = llm.worker_contexts[worker]
        task = context["subtask"]
        assert task["description"] == descriptions[worker]
        assert task["request_item_ids"]
        assert build_call[:4] == (
            question,
            descriptions[worker],
            tuple(task["deliverable_ids"]),
            inputs[worker],
        )
        assert [item.id for item in build_call[4]["request_items"]] == task["request_item_ids"]
        query = backend_request["query"]
        assert descriptions[worker] in query
        for deliverable in task["deliverable_ids"]:
            assert deliverable.replace("_", " ").lower() in query
        other = next(value for key, value in descriptions.items() if key != worker)
        assert other not in query
        assert query.count(question) == 1
        assert query.count(inputs[worker]) == 1
        assert query_event["payload"]["query"] == query
        assert query_event["payload"]["subtask_id"] == task["subtask_id"]
        assert query_event["payload"]["deliverable_ids"] == task["deliverable_ids"]
        assert query_event["payload"]["request_item_ids"] == task["request_item_ids"]
        assert query_event["payload"]["top_k"] == backend_request["top_k"] == config.retrieval_top_k
        assert backend_request["collection"] == config.special_collection
        call = by_id[query_event["parent_event_id"]]
        assert call["event_type"] == "tool_call"
        assert call["agent"] == worker
        assert call["payload"]["arguments"] == {"query": inputs[worker]}
        observed.append(
            {
                "worker": worker,
                "assigned_deliverables": task["deliverable_ids"],
                "builder_deliverables": build_call[2],
                "query": query,
            }
        )
    actual = [request["query"] for request in backend.requests]
    assert actual[0] != actual[1]
    print("B1 actual retrieval queries: " + json.dumps(observed, ensure_ascii=False))


def test_query_builder_can_preserve_distinct_tasks_and_deduplicates_exact_parts():
    builder = QueryBuilder()
    diagnosis = builder.build(
        "Shared question", "Diagnostic objective", "DIAGNOSIS_WITH_BASIS", "shared evidence"
    )
    treatment = builder.build(
        "Shared question", "Treatment objective", "TREATMENT_PLAN", "shared evidence"
    )
    assert diagnosis != treatment
    assert "Diagnostic objective diagnosis with basis" in diagnosis
    assert "Treatment objective treatment plan" in treatment
    assert (
        builder.build("Shared question", " shared   evidence ", "TREATMENT_PLAN", "SHARED EVIDENCE")
        == "Shared question shared evidence treatment plan"
    )


def test_same_worker_concurrent_subtasks_preserve_all_assigned_context(tmp_path):
    question = (
        "Synthetic fatigue; adult outpatient.\n"
        "1. Explain the diagnosis.\n"
        "2. Compare alternative causes.\n"
        "3. Recommend further tests."
    )
    spec = build_request_spec(question)
    assert len(spec.items) == 3
    contract = build_answer_contract(question)
    complexity = build_complexity_profile(question, contract)
    tasks = [
        Subtask(
            "causes",
            "Assess primary and alternative causes.",
            "diagnostic_agent",
            ["DIAGNOSIS_WITH_BASIS", "DIFFERENTIAL_DIAGNOSIS"],
            request_item_ids=[item.id for item in spec.items[:2]],
        ),
        Subtask(
            "tests",
            "Select additional investigations.",
            "diagnostic_agent",
            ["FURTHER_TESTS"],
            request_item_ids=[spec.items[2].id],
        ),
    ]
    trace = TraceRecorder(tmp_path)

    class OverlappingBackend(FakeRetrievalBackend):
        async def search(self, query, collection, top_k):
            result = await super().search(query, collection, top_k)
            if len(self.requests) == 2:
                self.both_started.set()
            await self.both_started.wait()
            return result

    class SubtaskLLM:
        def __init__(self):
            self.counts = {}

        async def complete(self, messages, **kwargs):
            task = json.loads(messages[1]["content"])["subtask"]
            task_id = task["subtask_id"]
            self.counts[task_id] = self.counts.get(task_id, 0) + 1
            if self.counts[task_id] == 1:
                return LLMResponse(
                    tool_calls=[
                        ToolCall(task_id, "clinical_guideline", {"query": "shared evidence"})
                    ]
                )
            return LLMResponse(
                content=json.dumps(
                    {
                        "answers": [
                            {"request_item_id": item, "answer": "A cautious response."}
                            for item in task["request_item_ids"]
                        ]
                    }
                )
            )

        async def close(self):
            pass

    backend = OverlappingBackend()
    llm = SubtaskLLM()
    config = RuntimeConfig(trace_dir=str(tmp_path), max_tool_calls=2)
    engine = NativeMedAgentEngine(config, llm=llm, retrieval_backend=backend)
    registry = engine._tool_registry(
        question, EvidenceBundle("", "", []), {"diagnostic_agent": {"clinical_guideline"}}
    )
    schemas = deepcopy(registry.schemas_for("diagnostic_agent"))
    loop = AgentLoop(llm, registry)

    async def run():
        backend.both_started = asyncio.Event()
        try:
            return await asyncio.wait_for(
                asyncio.gather(
                    *[
                        loop.execute(
                            DIAGNOSTIC_AGENT,
                            task,
                            spec,
                            contract,
                            EvidenceLedger(),
                            {"question": question},
                            [],
                            load_public_skills()["diagnostic_agent"],
                            trace,
                            complexity,
                            build_response_profile(complexity),
                        )
                        for task in tasks
                    ]
                ),
                timeout=5,
            )
        finally:
            await engine.close()

    results = asyncio.run(run())
    assert all(result.success for result in results)
    assert llm.counts == {"causes": 2, "tests": 2}
    assert registry.calls_for("diagnostic_agent") == 2
    assert (
        registry.schemas_for("diagnostic_agent") == schemas == [TOOL_SCHEMAS["clinical_guideline"]]
    )
    with pytest.raises(RuntimeError, match="budget exhausted"):
        asyncio.run(registry.execute("diagnostic_agent", "clinical_guideline", {"query": "third"}))
    assert len(backend.requests) == 2
    queries = {
        event["payload"]["subtask_id"]: event["payload"]
        for event in read_trace(trace.run_dir)
        if event["event_type"] == "retrieval_query"
    }
    for task in tasks:
        payload = queries[task.subtask_id]
        query = payload["query"]
        assert task.description in query
        assert "Synthetic fatigue; adult outpatient." in query
        assert payload["deliverable_ids"] == task.deliverable_ids
        assert payload["request_item_ids"] == task.request_item_ids
        for deliverable in task.deliverable_ids:
            assert deliverable.replace("_", " ").lower() in query
        for item in spec.items:
            assert (item.text in query) == (item.id in task.request_item_ids)
        assert any(request["query"] == query for request in backend.requests)
        other = next(value for value in tasks if value != task)
        assert other.description not in query
    assert queries["causes"]["query"] != queries["tests"]["query"]
    trace.record("final_answer", {"answer": "Offline answer."})
    trace.finish()
    assert replay(trace.run_dir)["final_answer"] == "Offline answer."


def test_context_cannot_override_model_arguments_or_execution_guards():
    context = ToolExecutionContext("t1", "Trusted task", ("TREATMENT_PLAN",), ())
    registry = ToolRegistry(max_calls=1)
    handler = Mock(return_value="ok")
    registry.register("clinical_guideline", handler, with_context=True)
    schemas = deepcopy(registry.schemas_for("diagnostic_agent"))

    with pytest.raises(PermissionError):
        asyncio.run(registry.execute("unknown_worker", "clinical_guideline", None, context=context))
    assert registry.calls_for("unknown_worker") == 0
    with pytest.raises(LookupError):
        asyncio.run(registry.execute("diagnostic_agent", "search_knowledge", None, context=context))
    assert registry.calls_for("diagnostic_agent") == 0
    for arguments in (
        {"query": "safe", "context": "forged"},
        {"query": "safe", "subtask_id": "forged"},
    ):
        # Independent Worker budgets, but the same trusted per-call context.
        worker = "diagnostic_agent" if "context" in arguments else "consultation_agent"
        with pytest.raises(TypeError, match="additional"):
            asyncio.run(registry.execute(worker, "clinical_guideline", arguments, context=context))
        assert registry.calls_for(worker) == 1
    handler.assert_not_called()
    with pytest.raises(RuntimeError, match="budget exhausted"):
        asyncio.run(
            registry.execute("diagnostic_agent", "clinical_guideline", None, context=context)
        )
    assert (
        asyncio.run(
            registry.execute(
                "research_agent", "clinical_guideline", {"query": "safe"}, context=context
            )
        )
        == "ok"
    )
    handler.assert_called_once_with(context, query="safe")
    assert registry.schemas_for("diagnostic_agent") == schemas
    filtered = ToolRegistry(capability_filter={"diagnostic_agent": set()})
    filtered.register("clinical_guideline", handler, with_context=True)
    with pytest.raises(PermissionError):
        asyncio.run(filtered.execute("diagnostic_agent", "clinical_guideline", {}, context=context))
    assert filtered.calls_for("diagnostic_agent") == 0


def test_legacy_handlers_and_retrieval_without_context_remain_usable(tmp_path):
    registry = ToolRegistry()
    handler = Mock(return_value="ordinary")
    registry.register("clinical_guideline", handler, with_context=True)
    registry.register("clinical_guideline", handler)  # Re-registration removes context opt-in.
    context = ToolExecutionContext("t", "Task", (), ())
    assert (
        asyncio.run(
            registry.execute(
                "diagnostic_agent", "clinical_guideline", {"query": "value"}, context=context
            )
        )
        == "ordinary"
    )
    handler.assert_called_once_with(query="value")
    backend = FakeRetrievalBackend()
    engine = NativeMedAgentEngine(RuntimeConfig(trace_dir=str(tmp_path)), retrieval_backend=backend)
    tools = engine._tool_registry(
        "Original context", EvidenceBundle("", "", []), {"diagnostic_agent": {"clinical_guideline"}}
    )

    async def run():
        try:
            return await tools.execute("diagnostic_agent", "clinical_guideline", {"query": "input"})
        finally:
            await engine.close()

    output = asyncio.run(run())
    assert output["query"] == backend.requests[0]["query"] == "Original context input"
