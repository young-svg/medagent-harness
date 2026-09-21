from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import AsyncClient, MockTransport, Request, Response

from medagent.llm import LLMResponse, OpenAICompatibleLLM, ScriptedLLM, ToolCall
from medagent.memory.session import SessionMemory
from medagent.observability.replay import read_trace
from medagent.observability.tracer import TraceRecorder
from medagent.retrieval.backend import (
    FakeRetrievalBackend,
    MilvusRetrievalBackend,
    OffRetrievalBackend,
)
from medagent.retrieval.collection_router import CollectionRouter
from medagent.retrieval.config import RetrievalConfig
from medagent.retrieval.evidence import EvidenceItem
from medagent.retrieval.factory import RetrievalConfigurationError, build_retrieval_backend
from medagent.runtime.config import RuntimeConfig
from medagent.runtime.native_engine import NativeMedAgentEngine
from medagent.skills.loader import load_public_skills


def _single_plan(agent: str = "diagnostic_agent") -> dict[str, object]:
    return {
        "subtasks": [
            {
                "subtask_id": "task-1",
                "description": "Complete the public runtime claim check.",
                "assigned_agent": agent,
            }
        ]
    }


def _request_text(request: dict[str, object]) -> str:
    return json.dumps(request["messages"], ensure_ascii=False)


@pytest.mark.asyncio
async def test_memory_is_injected_into_actual_planner_and_worker_requests(tmp_path: Path) -> None:
    llm = ScriptedLLM(
        [
            _single_plan(),
            "Answer one",
            _single_plan(),
            "Answer two",
            _single_plan(),
            "Answer three",
            _single_plan(),
            "Isolated answer",
        ]
    )
    engine = NativeMedAgentEngine(
        RuntimeConfig(trace_dir=str(tmp_path)),
        llm=llm,
        memory=SessionMemory(recent_limit=10),
    )

    await engine.analyze("CASE_ALPHA_TOKEN", "Assess the case", "shared")
    await engine.analyze("CASE_BETA_TOKEN", "Assess the case", "shared")
    third = await engine.analyze(
        "CORRECTION_ALPHA_PRIME_TOKEN", "Use this correction", "shared"
    )
    await engine.analyze("ISOLATED_CASE_TOKEN", "Assess separately", "isolated")

    for index in (2, 3):
        assert "CASE_ALPHA_TOKEN" in _request_text(llm.requests[index])
    for index in (4, 5):
        request_text = _request_text(llm.requests[index])
        assert "CASE_ALPHA_TOKEN" in request_text
        assert "CORRECTION_ALPHA_PRIME_TOKEN" in request_text
        assert request_text.index("CASE_ALPHA_TOKEN") < request_text.index(
            "CORRECTION_ALPHA_PRIME_TOKEN"
        )
    for index in (6, 7):
        request_text = _request_text(llm.requests[index])
        assert "ISOLATED_CASE_TOKEN" in request_text
        assert "CASE_ALPHA_TOKEN" not in request_text
        assert "CASE_BETA_TOKEN" not in request_text

    events = read_trace(tmp_path / third["run_id"])
    memory_read = next(event for event in events if event["event_type"] == "memory_read")
    assert memory_read["payload"]["injected_messages"]
    assert "CORRECTION_ALPHA_PRIME_TOKEN" in memory_read["payload"]["current_input"]["content"]
    await engine.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("memory_enabled", [True, False])
async def test_empty_or_disabled_memory_runs_with_explicit_empty_context(
    tmp_path: Path, memory_enabled: bool
) -> None:
    llm = ScriptedLLM([_single_plan(), "Fresh-session answer"])
    engine = NativeMedAgentEngine(
        RuntimeConfig(trace_dir=str(tmp_path)),
        llm=llm,
        memory=SessionMemory(enabled=memory_enabled),
    )
    await engine.analyze("FRESH_CASE_TOKEN", "Assess", "fresh")

    assert "Bounded session context (background only): []" in _request_text(llm.requests[0])
    worker_payload = json.loads(str(llm.requests[1]["messages"][-1]["content"]))
    assert worker_payload["bounded_session_context"] == []
    await engine.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("agent", "skill_key"),
    [
        ("diagnostic_agent", "diagnostic_agent"),
        ("consultation_agent", "consultation_agent"),
        ("research_agent", "research_agent"),
    ],
)
async def test_worker_skill_is_in_actual_system_message(
    tmp_path: Path, agent: str, skill_key: str
) -> None:
    llm = ScriptedLLM([_single_plan(agent), "Role-specific answer"])
    engine = NativeMedAgentEngine(RuntimeConfig(trace_dir=str(tmp_path / agent)), llm=llm)
    result = await engine.analyze("Synthetic role case", "Complete the role task", agent)

    worker_system = str(llm.requests[1]["messages"][0]["content"])
    skill = load_public_skills()[skill_key]
    assert skill.instructions in worker_system
    events = read_trace(tmp_path / agent / result["run_id"])
    run_start = events[0]["payload"]
    assert run_start["skills"][skill_key] == {
        "skill_name": skill.name,
        "skill_sha256": skill.sha256,
    }
    await engine.close()


@pytest.mark.asyncio
async def test_synthesis_skill_is_in_actual_system_message(tmp_path: Path) -> None:
    llm = ScriptedLLM(
        [
            {
                "subtasks": [
                    {
                        "subtask_id": "d",
                        "description": "Assess",
                        "assigned_agent": "diagnostic_agent",
                    },
                    {
                        "subtask_id": "r",
                        "description": "Research",
                        "assigned_agent": "research_agent",
                    },
                ]
            },
            "Diagnostic draft",
            "Research draft",
            "Combined draft",
        ]
    )
    engine = NativeMedAgentEngine(RuntimeConfig(trace_dir=str(tmp_path)), llm=llm)
    result = await engine.analyze(
        "Synthetic synthesis case", "Diagnosis and research", "synthesis"
    )

    synthesis_skill = load_public_skills()["synthesizer"]
    matching = [
        request
        for request in llm.requests
        if synthesis_skill.instructions in str(request["messages"][0]["content"])
    ]
    assert len(matching) == 1
    events = read_trace(tmp_path / result["run_id"])
    synthesis_request = next(
        event
        for event in events
        if event["event_type"] == "llm_request" and event["stage"] == "synthesis"
    )
    synthesis_response = next(
        event
        for event in events
        if event["event_type"] == "llm_response" and event["stage"] == "synthesis"
    )
    assert synthesis_skill.instructions in synthesis_request["payload"]["messages"][0]["content"]
    assert synthesis_response["payload"]["content"] == "Combined draft"
    await engine.close()


def test_retrieval_backend_factory_modes_and_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    assert isinstance(
        build_retrieval_backend(RuntimeConfig(retrieval_mode="off")), OffRetrievalBackend
    )
    assert isinstance(
        build_retrieval_backend(RuntimeConfig(retrieval_mode="fake")), FakeRetrievalBackend
    )
    with pytest.raises(RetrievalConfigurationError, match="MEDAGENT_MILVUS_URI"):
        build_retrieval_backend(RuntimeConfig(retrieval_mode="milvus"))

    monkeypatch.setattr("medagent.retrieval.factory.find_spec", lambda package: None)
    with pytest.raises(RetrievalConfigurationError, match="optional packages"):
        build_retrieval_backend(
            RuntimeConfig(retrieval_mode="milvus", milvus_uri="http://milvus.invalid")
        )

    router = CollectionRouter(
        RetrievalConfig(
            generic_collection="configured_generic",
            special_collection="configured_special",
        )
    )
    for tool in ("clinical_guideline", "disease_code", "recommend_lifestyle"):
        assert router.route(tool).collection == "configured_special"
    for tool in ("search_knowledge", "deep_research"):
        assert router.route(tool).collection == "configured_generic"


@pytest.mark.asyncio
async def test_fake_rag_factory_drives_real_tool_retrieval_chain(tmp_path: Path) -> None:
    llm = ScriptedLLM(
        [
            _single_plan("research_agent"),
            LLMResponse(
                tool_calls=[ToolCall("rag-1", "search_knowledge", {"query": "assessment"})]
            ),
            "Evidence-aware answer",
        ]
    )
    config = RuntimeConfig(
        trace_dir=str(tmp_path),
        retrieval_mode="fake",
        generic_collection="public_generic_test",
        special_collection="public_special_test",
    )
    engine = NativeMedAgentEngine(config, llm=llm)
    assert isinstance(engine.retrieval_backend, FakeRetrievalBackend)
    engine.retrieval_backend.items = [
        EvidenceItem(
            "public-evidence-1",
            "doc-1",
            "source-1",
            "Public reference",
            "Assessment",
            "Synthetic source",
            None,
            "Synthetic admitted evidence.",
            0.92,
            1,
        )
    ]

    result = await engine.analyze("Synthetic retrieval case", "Find evidence", "rag")
    assert engine.retrieval_backend.requests[0]["collection"] == "public_generic_test"
    assert any(
        schema["function"]["name"] == "search_knowledge"
        for schema in llm.requests[1]["tools"]
    )
    events = read_trace(tmp_path / result["run_id"])
    query = next(event for event in events if event["event_type"] == "retrieval_query")
    retrieval = next(event for event in events if event["event_type"] == "retrieval_result")
    assert query["payload"]["routing_reason"] == "general_clinical_corpus"
    assert query["payload"]["collection"] == "public_generic_test"
    assert retrieval["payload"]["raw_candidates"][0]["evidence_id"] == "public-evidence-1"
    assert retrieval["payload"]["admission"][0]["admitted"] is True
    assert retrieval["payload"]["compact_evidence"][0]["evidence_id"] == "public-evidence-1"
    await engine.close()


@pytest.mark.asyncio
async def test_full_llm_trace_and_central_redaction(tmp_path: Path) -> None:
    api_key = "integration-secret-key-987"
    bearer = "integration-bearer-token-654"
    llm = ScriptedLLM(
        [
            _single_plan(),
            LLMResponse(
                "Raw assistant tool preface",
                [ToolCall("tool-1", "assess_risk", {"symptoms": "fatigue", "api_key": api_key})],
                {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
                "tool_calls",
                "resolved-test-model",
            ),
            LLMResponse(
                "Raw final worker response",
                usage={"prompt_tokens": 4, "completion_tokens": 3, "total_tokens": 7},
                finish_reason="stop",
                model="resolved-test-model",
            ),
        ]
    )
    engine = NativeMedAgentEngine(RuntimeConfig(trace_dir=str(tmp_path)), llm=llm)
    result = await engine.analyze(
        f"Patient has secretory diarrhea. Authorization: Bearer {bearer}; api_key={api_key}",
        "Assess safely",
        "trace",
    )

    trace_path = tmp_path / result["run_id"] / "trace.jsonl"
    raw_trace = trace_path.read_text(encoding="utf-8")
    assert api_key not in raw_trace
    assert bearer not in raw_trace
    assert "secretory diarrhea" in raw_trace

    events = read_trace(trace_path.parent)
    requests = [event for event in events if event["event_type"] == "llm_request"]
    responses = [event for event in events if event["event_type"] == "llm_response"]
    assert {event["stage"] for event in requests} == {"planning", "worker"}
    assert all(event["payload"]["messages"] for event in requests)
    assert requests[1]["payload"]["tools"]
    assert requests[1]["payload"]["tool_choice"] == "auto"
    assert requests[1]["payload"]["requested_model"] == "scripted-test-model"
    assert requests[1]["payload"]["resolved_model"] == "scripted-test-model"
    assert requests[1]["payload"]["temperature"] == 0.0
    assert requests[1]["payload"]["max_tokens"] == 1200
    assert requests[1]["payload"]["request_timestamp"]

    tool_response = next(
        event
        for event in responses
        if event["payload"]["content"] == "Raw assistant tool preface"
    )
    assert tool_response["payload"]["tool_calls"][0]["arguments"]["api_key"] == "[REDACTED]"
    assert tool_response["payload"]["usage"]["total_tokens"] == 5
    assert tool_response["payload"]["finish_reason"] == "tool_calls"
    assert tool_response["payload"]["resolved_model"] == "resolved-test-model"
    assert isinstance(tool_response["payload"]["latency_ms"], float)
    assert tool_response["payload"]["error"] is None
    assert any(
        event["payload"]["content"] == "Raw final worker response" for event in responses
    )
    await engine.close()


def test_redaction_applies_at_single_trace_write_boundary(tmp_path: Path) -> None:
    trace = TraceRecorder(tmp_path, "redaction-boundary")
    trace.record(
        "context_built",
        {
            "cookie": "session-cookie-value",
            "nested": {"password": "password-value"},
            "clinical_text": "A secretory process is part of the differential.",
        },
    )
    persisted = (tmp_path / "redaction-boundary" / "trace.jsonl").read_text(encoding="utf-8")
    assert "session-cookie-value" not in persisted
    assert "password-value" not in persisted
    assert "secretory process" in persisted


@pytest.mark.asyncio
async def test_milvus_backend_loads_collection_and_resolves_unique_vector_field() -> None:
    class StubClient:
        def __init__(self) -> None:
            self.loaded: list[str] = []
            self.anns_field = ""
            self.closed = False

        def describe_collection(self, *, collection_name: str) -> dict[str, object]:
            assert collection_name == "configured_collection"
            return {
                "fields": [
                    {"name": "id", "type": "INT64"},
                    {"name": "vector", "type": "FLOAT_VECTOR"},
                ]
            }

        def load_collection(self, *, collection_name: str) -> None:
            self.loaded.append(collection_name)

        def search(self, **kwargs: object) -> list[list[dict[str, object]]]:
            self.anns_field = str(kwargs["anns_field"])
            return [
                [
                    {
                        "id": 7,
                        "distance": 0.91,
                        "entity": {
                            "content": "evidence",
                            "metadata": json.dumps(
                                {
                                    "doc_id": "doc-7",
                                    "disease": "Synthetic title",
                                    "type": "overview",
                                    "source": "Synthetic source",
                                }
                            ),
                        },
                    }
                ]
            ]

        def close(self) -> None:
            self.closed = True

    backend = object.__new__(MilvusRetrievalBackend)
    backend._client = StubClient()
    backend.embed_query = lambda text: [0.1, 0.2]
    backend.vector_field = "embedding"
    backend.output_fields = ["text"]
    backend.resolved_vector_fields = {}

    items = await backend.search("query", "configured_collection", 1)
    assert backend._client.loaded == ["configured_collection"]
    assert backend._client.anns_field == "vector"
    assert backend.resolved_vector_fields == {"configured_collection": "vector"}
    assert items[0].text == "evidence"
    assert items[0].evidence_id == "milvus-7"
    assert items[0].document_id == "doc-7"
    assert items[0].title == "Synthetic title"
    assert items[0].section == "overview"
    assert items[0].source == "Synthetic source"
    await backend.close()
    assert backend._client.closed is True


@pytest.mark.asyncio
async def test_openai_client_preserves_nested_provider_usage() -> None:
    def handler(request: Request) -> Response:
        assert request.url.path.endswith("/chat/completions")
        return Response(
            200,
            json={
                "model": "resolved-provider-model",
                "choices": [
                    {"message": {"content": "answer"}, "finish_reason": "stop"}
                ],
                "usage": {
                    "prompt_tokens": 4,
                    "completion_tokens": 2,
                    "total_tokens": 6,
                    "prompt_tokens_details": {"cached_tokens": 1},
                },
            },
        )

    client = OpenAICompatibleLLM(
        base_url="https://provider.invalid/v1",
        api_key="fake-test-key",
        model="requested-model",
    )
    await client._client.aclose()
    client._client = AsyncClient(transport=MockTransport(handler))
    response = await client.complete([{"role": "user", "content": "hello"}])
    assert response.usage["total_tokens"] == 6
    assert response.usage["prompt_tokens_details"] == {"cached_tokens": 1}
    assert response.model == "resolved-provider-model"
    await client.close()
