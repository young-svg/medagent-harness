"""No network: existing OpenAICompatibleLLM talks to httpx.MockTransport."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from eval.w7.budget import BatchBudget, BatchStopped, MeteredLLM, RunBudget
from eval.w7.live import experiment, live_config, probe_json, probe_tool
from eval.w7.runner import run_case
from eval.w7.scripted import ANSWERS, SmokeLLM
from medagent.llm.client import OpenAICompatibleLLM


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setenv("MEDAGENT_LLM_BASE_URL", "https://provider.invalid/v1")
    monkeypatch.setenv("MEDAGENT_LLM_API_KEY", "test-private-key-not-for-logs")
    monkeypatch.setenv("MEDAGENT_LLM_MODEL", "fixed-test-model")
    import eval.w7.live as live

    original = live.make_client
    requests = []
    responder = {"http_error": None, "reply": None}

    async def transport(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if responder["http_error"]:
            return httpx.Response(
                responder["http_error"],
                json={
                    "error": {
                        "type": "invalid_request_error",
                        "message": "private body must not be recorded",
                    }
                },
            )
        if responder["reply"]:
            value = responder["reply"](payload)
        else:
            value = {"content": '{"ok":true}'}
        tool_calls = value.get("tool_calls")
        return httpx.Response(
            200,
            json={
                "model": "resolved-model-v1",
                "usage": {
                    "prompt_tokens": 40,
                    "completion_tokens": 10,
                    "total_tokens": 50,
                    "prompt_tokens_details": {"cached_tokens": 5},
                },
                "choices": [
                    {"message": value, "finish_reason": "tool_calls" if tool_calls else "stop"}
                ],
            },
        )

    def factory(config, batch):
        client = original(config, batch)
        # Keep the real parser, payload construction, hooks and lifecycle; only replace transport.
        client._client._transport = httpx.MockTransport(transport)
        client._client._mounts.clear()
        return client

    monkeypatch.setattr(live, "make_client", factory)
    return requests, responder


def tool_reply(payload):
    if not any(m["role"] == "tool" for m in payload["messages"]):
        return {
            "content": "",
            "tool_calls": [
                {
                    "id": "probe-paper",
                    "type": "function",
                    "function": {
                        "name": "search_knowledge",
                        "arguments": json.dumps(
                            {"query": "SYNTHETIC cough papers", "request_item_id": "RQ2"}
                        ),
                    },
                }
            ],
        }
    return {
        "content": json.dumps(
            {"answers": [{"request_item_id": "RQ2", "answer": ANSWERS["E03"]["RQ2"]}]}
        )
    }


@pytest.mark.asyncio
async def test_live_config_never_falls_back(monkeypatch, tmp_path):
    monkeypatch.delenv("MEDAGENT_LLM_API_KEY", raising=False)
    with pytest.raises(ValueError, match="no_fallback"):
        await experiment(tmp_path / "new")
    assert not (tmp_path / "new").exists()


@pytest.mark.asyncio
async def test_probe_json_real_client_usage_and_http_status(provider, tmp_path):
    requests, _ = provider
    batch = BatchBudget(3)
    row = await probe_json(tmp_path, live_config(), batch)
    assert row["passed"] and len(requests) == batch.attempts == 1
    model = row["models"][0]
    assert model["usage_origin"] == "provider_reported" and model["http_status"] == 200
    assert model["resolved_model"] == "resolved-model-v1"
    assert model["provider_usage"]["prompt_tokens_details"]["cached_tokens"] == 5
    assert row["metrics"]["input_tokens"] == 40


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403, 429, 500])
async def test_http_failure_stops_before_p2_or_pilot_and_never_retries(provider, tmp_path, status):
    requests, responder = provider
    responder["http_error"] = status
    row = await experiment(tmp_path / "run")
    assert len(requests) == row["probe_attempts"] == 1
    assert row["pilot_attempts"] == 0 and row["pilot_not_started"]
    assert row["probes"][0]["models"][0]["http_status"] == status
    assert row["probes"][0]["metrics"]["input_tokens"] == "UNKNOWN"
    files = "".join(p.read_text(encoding="utf-8") for p in (tmp_path / "run").rglob("*.json"))
    assert "test-private-key-not-for-logs" not in files
    assert "private body must not be recorded" not in files


@pytest.mark.asyncio
async def test_probe_tool_runs_actual_handler_and_native_continuation(provider, tmp_path):
    requests, responder = provider
    responder["reply"] = tool_reply
    row = await probe_tool(tmp_path, live_config(), BatchBudget(3))
    assert row["passed"] and len(requests) == 2
    assert row["metrics"]["handler_executions"] == row["metrics"]["tool_attempts"] == 1
    continuation = requests[1]["messages"]
    assert continuation[-1]["role"] == "tool" and continuation[-1]["tool_call_id"] == "probe-paper"
    assert continuation[-2]["tool_calls"][0]["id"] == "probe-paper"


@pytest.mark.asyncio
async def test_continuation_http400_never_executes_handler_twice(provider, tmp_path):
    requests, responder = provider

    def first_then_error(payload):
        value = tool_reply(payload)
        responder["http_error"] = 400
        return value

    responder["reply"] = first_then_error
    batch = BatchBudget(3)
    row = await probe_tool(tmp_path, live_config(), batch)
    assert not row["passed"] and len(requests) == batch.attempts == 2
    assert row["metrics"]["handler_executions"] == 1
    assert row["models"][1]["http_status"] == 400
    assert batch.stop_reason == "http_400"


@pytest.mark.asyncio
async def test_shared_batch_atomic_admission_across_runs(provider):
    requests, _ = provider
    from eval.w7.live import make_client

    batch = BatchBudget(3)
    clients = [
        MeteredLLM(
            make_client(live_config(), batch), RunBudget(batch=batch), stage_override="probe_json"
        )
        for _ in range(10)
    ]
    try:
        results = await asyncio.gather(
            *(c.complete([{"role": "system", "content": "JSON"}]) for c in clients),
            return_exceptions=True,
        )
        assert batch.attempts == len(requests) == 3
        assert sum(isinstance(r, BatchStopped) for r in results) == 7
    finally:
        for client in clients:
            await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("architecture", ["A", "B", "C"])
async def test_live_adapter_executes_real_client_without_script_or_presentation(
    provider,
    tmp_path,
    monkeypatch,
    architecture,
):
    requests, responder = provider

    def answer(payload):
        system = payload["messages"][0]["content"]
        if system.startswith("You are the public MedAgent centralized planner"):
            return {
                "content": json.dumps(
                    {
                        "subtasks": [
                            {
                                "subtask_id": "single",
                                "assigned_agent": "diagnostic_agent",
                                "description": "Explain supplied interval",
                                "request_item_ids": ["RQ1"],
                                "deliverable_ids": ["COMPREHENSIVE_CASE_ANALYSIS"],
                            }
                        ]
                    }
                )
            }
        if system.startswith("Role:"):
            return {
                "content": json.dumps(
                    {"answers": [{"request_item_id": "RQ1", "answer": ANSWERS["S02"]["RQ1"]}]}
                )
            }
        return {
            "content": json.dumps(
                {
                    "items": [
                        {
                            "request_item_id": "RQ1",
                            "status": "satisfied",
                            "evidence": ANSWERS["S02"]["RQ1"],
                            "reason": "offline mock verdict",
                        }
                    ],
                    "restrictions": [],
                    "guardrail": [],
                }
            )
        }

    responder["reply"] = answer

    def forbidden(*args, **kwargs):
        raise AssertionError("live must not create ScriptedLLM")

    monkeypatch.setattr(SmokeLLM, "__init__", forbidden)
    batch = BatchBudget(144)
    row, _ = await run_case("S02", architecture, tmp_path, mode="live", batch=batch)
    assert row["status"] == "completed" and row["model_mode"] == "LIVE_PROVIDER"
    assert not row["presentation_llm_enabled"]
    assert row["independent_score"]["status"] == "UNSCORED"
    assert batch.attempts == len(requests) == (2 if architecture == "A" else 3)
    assert all(m["usage_origin"] == "provider_reported" for m in row["models"])
    assert row["metrics"]["input_tokens"] == len(requests) * 40
    assert isinstance(OpenAICompatibleLLM, type)
