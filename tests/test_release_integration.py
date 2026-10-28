"""Cross-feature regressions for the release runtime merged with A1-C1."""

import json

import httpx
import pytest

from medagent.context.contract import build_answer_contract
from medagent.context.request_spec import build_request_spec
from medagent.llm import LLMResponse, ScriptedLLM
from medagent.observability.replay import read_trace, replay
from medagent.planning.complexity import build_complexity_profile
from medagent.planning.planner import Planner, apply_contract_policy
from medagent.runtime.config import RuntimeConfig
from medagent.runtime.native_engine import NativeMedAgentEngine
from tests.test_presentation_transform import sectioned_output

QUESTION = "1. Give a diagnosis.\n2. Give a treatment plan based on that diagnosis."
DIAGNOSIS = "Synthetic assessment Alpha is provisional."
CARE = "Synthetic care Beta requires qualified review."


def answers(item_id, text):
    return {"answers": [{"request_item_id": item_id, "answer": text}]}


def dependency_plan():
    return {
        "subtasks": [
            {
                "subtask_id": "diagnose",
                "description": "Give a provisional assessment.",
                "assigned_agent": "diagnostic_agent",
                "request_item_ids": ["RQ1"],
                "deliverable_ids": ["DIAGNOSIS_WITH_BASIS"],
            },
            {
                "subtask_id": "treat",
                "description": "Use the upstream assessment for care.",
                "assigned_agent": "consultation_agent",
                "request_item_ids": ["RQ2"],
                "deliverable_ids": ["TREATMENT_PLAN"],
                "depends_on": ["diagnose"],
            },
        ]
    }


class OfflineFailureLLM(ScriptedLLM):
    async def complete(self, messages, **kwargs):
        if self.responses and isinstance(self.responses[0], Exception):
            self.requests.append({"messages": messages, **kwargs})
            raise self.responses.popleft()
        return await super().complete(messages, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("recover", [True, False])
@pytest.mark.parametrize("transient", [True, False])
async def test_protocol_recovery_and_retry_preserve_handoff_gate(tmp_path, recover, transient):
    malformed = '{"answers": [{"request_item_id": "RQ1", "answer": "' + DIAGNOSIS
    responses = [dependency_plan()]
    if transient:
        responses.append(httpx.ConnectError("offline transient failure"))
    responses.extend(
        [
            LLMResponse(content=malformed, finish_reason="stop"),
            answers("RQ1", "<analysis>PRIVATE_MARKER</analysis>" + DIAGNOSIS)
            if recover else "Invalid serialization repair.",
        ]
    )
    if recover:
        responses.extend([answers("RQ2", CARE), {"answers": []}])
    llm = OfflineFailureLLM(responses)
    engine = NativeMedAgentEngine(
        RuntimeConfig(
            trace_dir=str(tmp_path), worker_infrastructure_retry_base_delay_seconds=0
        ),
        llm=llm,
    )
    try:
        result = await engine.analyze("Synthetic fixture only.", QUESTION, "integration")
    finally:
        await engine.close()
    events = read_trace(tmp_path / result["run_id"])
    by_id = {event["event_id"]: event for event in events}
    assert not llm.responses
    assert result["trace"]["protocol_recovery_count"] == 1
    assert result["trace"]["protocol_recovery_success_count"] == int(recover)
    assert result["trace"]["worker_infrastructure_retry_count"] == int(transient)
    assert result["trace"]["llm_calls"] == len(llm.requests) == (
        (5 if recover else 3) + int(transient)
    )
    assert result["trace"]["tool_calls"] == 0
    assert replay(tmp_path / result["run_id"])["final_answer"] == result["final_answer"]
    child_requests = [
        e for e in events if e["event_type"] == "llm_request"
        and e["stage"] == "worker" and e["agent"] == "consultation_agent"
    ]
    if recover:
        assert result["status"] == result["trace"]["status"] == "completed"
        assert result["user_request_complete"]
        assert result["final_answer"] == DIAGNOSIS + "\n\n" + CARE
        assert len(child_requests) == 1
        handoff = next(e for e in events if e["event_type"] == "worker_handoff")
        source = by_id[handoff["payload"]["source_event_id"]]
        assert source["event_type"] == "worker_draft"
        assert source["payload"]["subtask_id"] == "diagnose"
        assert source["payload"]["protocol_recovery_success_count"] == 1
        response = by_id[source["parent_event_id"]]
        assert response["event_type"] == "llm_response"
        assert response["stage"] == "worker_protocol_recovery"
        assert handoff["parent_event_id"] == source["event_id"]
        assert "PRIVATE_MARKER" not in json.dumps(handoff["payload"])
        assert "PRIVATE_MARKER" not in json.dumps(child_requests[0]["payload"])
        assert "PRIVATE_MARKER" not in result["final_answer"]
    else:
        assert result["status"] == result["trace"]["status"] == "incomplete"
        assert not result["user_request_complete"]
        assert result["final_answer"] == ""
        assert result["final_request_item_answers"] == {}
        assert child_requests == []
        assert any(e["event_type"] == "dependency_blocked" for e in events)
        assert not any(e["event_type"] == "worker_handoff" for e in events)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["complete", "presentation_invalid", "sanitized_empty"])
async def test_release_presentation_respects_final_coverage(tmp_path, monkeypatch, mode):
    if mode == "sanitized_empty":
        monkeypatch.setattr("medagent.runtime.native_engine.sanitize_answer", lambda _: "")
    plan = {"subtasks": [{"assigned_agent": "diagnostic_agent", "description": "Assess"}]}
    llm = ScriptedLLM([plan, answers("RQ1", DIAGNOSIS)])
    presentation_llm = ScriptedLLM(
        ["Invalid presentation." if mode == "presentation_invalid" else sectioned_output(["RQ1"])]
    )
    engine = NativeMedAgentEngine(
        RuntimeConfig(trace_dir=str(tmp_path)), llm=llm, presentation_llm=presentation_llm
    )
    try:
        result = await engine.analyze("Synthetic fixture only.", "Assess", "presentation")
    finally:
        await engine.close()
    events = read_trace(tmp_path / result["run_id"])
    assert result["presentation"]["professional_answer"] == result["final_answer"]
    assert result["presentation"]["execution_summary"]["user_request_complete"] == (
        result["user_request_complete"]
    )
    assert llm.closed and presentation_llm.closed
    if mode == "sanitized_empty":
        assert result["status"] == result["trace"]["status"] == "incomplete"
        assert not result["user_request_complete"]
        assert result["final_answer"] == ""
        assert result["final_request_item_answers"] == {}
        assert presentation_llm.requests == []
        assert result["trace"]["llm_calls"] == 2
        assert not any(e["event_type"] == "presentation_transform" for e in events)
    else:
        assert result["status"] == result["trace"]["status"] == "completed"
        assert result["user_request_complete"]
        assert result["final_answer"] == DIAGNOSIS
        assert result["final_request_item_answers"] == {"RQ1": DIAGNOSIS}
        assert len(presentation_llm.requests) == 1
        assert result["trace"]["llm_calls"] == 3
        transform = next(e for e in events if e["event_type"] == "presentation_transform")
        assert transform["payload"]["status"] == (
            "fallback" if mode == "presentation_invalid" else "success"
        )


def test_release_reconciliation_preserves_dependencies():
    raw = dependency_plan()
    raw["subtasks"][1]["deliverable_ids"] = []
    spec = build_request_spec(QUESTION)
    contract = build_answer_contract(QUESTION)
    plan = apply_contract_policy(
        Planner.parse(json.dumps(raw), "diagnostic_agent"),
        contract,
        build_complexity_profile(QUESTION, contract),
        spec,
    )
    assert [task.subtask_id for task in plan.subtasks] == ["diagnose", "treat"]
    assert plan.subtasks[1].depends_on == ["diagnose"]
    assert "TREATMENT_PLAN" in plan.subtasks[1].deliverable_ids
    assert "preserved_dependency_graph" in plan.policy_actions


def test_integrated_config_keeps_release_defaults_and_memory_budget(monkeypatch):
    for key in (
        "MEDAGENT_WORKER_MAX_INFRA_RETRIES",
        "MEDAGENT_WORKER_INFRA_RETRY_BASE_DELAY_SECONDS",
        "MEDAGENT_MEMORY_TOKEN_BUDGET",
    ):
        monkeypatch.delenv(key, raising=False)
    config = RuntimeConfig.from_env()
    assert config.worker_max_infrastructure_retries == 2
    assert config.worker_infrastructure_retry_base_delay_seconds == 1.0
    assert config.memory_token_budget == 4096
    monkeypatch.setenv("MEDAGENT_WORKER_MAX_INFRA_RETRIES", "3")
    monkeypatch.setenv("MEDAGENT_MEMORY_TOKEN_BUDGET", "0")
    config = RuntimeConfig.from_env()
    assert config.worker_max_infrastructure_retries == 3
    assert config.memory_token_budget == 0
