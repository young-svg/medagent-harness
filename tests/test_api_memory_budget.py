"""Exercise the real API module initialization without replacing its Coordinator."""

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from medagent.llm import ScriptedLLM
from medagent.memory.session import SessionMemory, estimate_history_tokens
from medagent.observability.replay import read_trace
from medagent.runtime.config import RuntimeConfig
from medagent.runtime.coordinator import Coordinator


@pytest.mark.parametrize("setting, expected", [("0", 0), ("96", 96), (None, 4096), ("None", None)])
def test_api_initialization_uses_budget_and_keeps_session_memory(
    tmp_path, monkeypatch, setting, expected
):
    for key, value in {
        "MEDAGENT_RUNTIME_MODE": "native",
        "MEDAGENT_TRACE_DIR": str(tmp_path),
        "MEDAGENT_LLM_BASE_URL": "",
        "MEDAGENT_LLM_API_KEY": "",
        "MEDAGENT_RETRIEVAL_MODE": "off",
        "MEDICAL_KB_ENABLED": "false",
    }.items():
        monkeypatch.setenv(key, value)
    if setting is None:
        monkeypatch.delenv("MEDAGENT_MEMORY_TOKEN_BUDGET", raising=False)
    else:
        monkeypatch.setenv("MEDAGENT_MEMORY_TOKEN_BUDGET", setting)

    # Execute main.py under a test-only module name; do not reload or replace the
    # shared api.main used by other tests, and do not bypass its startup wiring.
    path = Path(__file__).resolve().parents[1] / "api" / "main.py"
    spec = importlib.util.spec_from_file_location("api._memory_budget_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    coordinator = module._coordinator
    memory = coordinator.engine.memory
    assert module._config.memory_token_budget == expected
    assert memory.token_budget == expected

    plan = {"subtasks": [{"description": "Assess", "assigned_agent": "diagnostic_agent"}]}
    llm = ScriptedLLM(
        [
            plan,
            "FIRST_SESSION_A_ANSWER.",
            plan,
            "SECOND_SESSION_A_ANSWER.",
            plan,
            "ISOLATED_SESSION_B_ANSWER.",
        ]
    )
    coordinator.engine.llm = llm
    results = []
    with TestClient(module.app) as client:
        for description, session in [
            ("FIRST_SESSION_A_CASE", "session-a"),
            ("SECOND_SESSION_A_CASE", "session-a"),
            ("ISOLATED_SESSION_B_CASE", "session-b"),
        ]:
            response = client.post(
                "/api/analyze",
                json={
                    "description": description,
                    "question": "Assess",
                    "session_id": session,
                },
            )
            assert response.status_code == 200
            body = response.json()
            assert body["status"] == "completed"
            results.append(body)
            assert module._coordinator is coordinator
            assert coordinator.engine.memory is memory
    assert llm.closed
    assert coordinator.engine._closed
    assert len(llm.requests) == 6
    for index, result in enumerate(results):
        read = next(
            event["payload"]
            for event in read_trace(tmp_path / result["run_id"])
            if event["event_type"] == "memory_read"
        )
        history = read["injected_messages"]
        assert read["history_token_budget"] == expected
        assert read["estimated_history_tokens"] == estimate_history_tokens(history)
        if expected is not None:
            assert read["estimated_history_tokens"] <= expected
        worker = json.loads(llm.requests[index * 2 + 1]["messages"][1]["content"])
        assert worker["bounded_session_context"] == history
        planner = llm.requests[index * 2]["messages"][1]["content"]
        assert "Bounded session context (background only): " + json.dumps(history) in planner
        assert read["current_input"]["content"] == (
            worker["current_context"]["description"] + "\nQuestion: Assess"
        )
        if index != 1 or expected == 0:
            assert history == []
        else:
            assert "FIRST_SESSION_A_ANSWER." in json.dumps(history)
            assert "SECOND_SESSION_A_CASE" not in json.dumps(history)
            if expected == 96:
                assert history == [{"role": "assistant", "content": "FIRST_SESSION_A_ANSWER."}]
                assert read["budget_trimmed_history_messages"] == 1
        if index == 2:
            assert "SESSION_A" not in json.dumps(worker)
    # Read-time budget zero must not disable storage, nor introduce per-request Memory.
    assert len(memory._messages["session-a"]) == 4
    assert len(memory._messages["session-b"]) == 2


def test_coordinator_preserves_explicit_custom_memory_configuration(tmp_path):
    memory = SessionMemory(token_budget=None, recent_limit=3, enabled=False)
    coordinator = Coordinator(
        RuntimeConfig(trace_dir=str(tmp_path), memory_token_budget=0), memory=memory
    )
    try:
        assert coordinator.engine.memory is memory
        assert memory.token_budget is None
        assert memory.recent_limit == 3
        assert memory.enabled is False
    finally:
        asyncio.run(coordinator.close())
