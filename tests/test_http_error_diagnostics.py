"""Offline HTTP diagnostics, privacy and tool-continuation regressions."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from medagent.llm.client import OpenAICompatibleLLM
from medagent.llm.generation import is_retryable_infrastructure_error
from medagent.observability.replay import read_trace
from medagent.retrieval.backend import FakeRetrievalBackend
from medagent.runtime.config import RuntimeConfig
from medagent.runtime.native_engine import NativeMedAgentEngine

KEY = "sk-offline-http-diagnostic-secret"
PRIVATE = "BODY_ONLY_SYNTHETIC_PRIVATE_TEXT_7Z"


async def mock_client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> OpenAICompatibleLLM:
    client = OpenAICompatibleLLM(
        base_url=f"https://provider.invalid/{KEY}/v1", api_key=KEY, model="offline-test"
    )
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


def provider_error() -> dict:
    return {
        "error": {
            "type": "invalid_request_error",
            "code": "missing_required_parameter",
            "param": "messages[3].tool_call_id",
            "message": f"Missing required parameter: tool_call_id. {PRIVATE} Bearer {KEY}",
            "request": {"api_key": KEY, "content": PRIVATE},
        },
        "debug": PRIVATE,
    }


@pytest.mark.asyncio
async def test_http_400_retains_allowlisted_metadata_without_body_key_or_url() -> None:
    client = await mock_client(lambda request: httpx.Response(400, json=provider_error()))
    try:
        with pytest.raises(httpx.HTTPStatusError) as caught:
            await client.complete([{"role": "user", "content": "Synthetic input"}])
        error = caught.value
        details = error.http_diagnostics
        assert details["status_code"] == 400
        assert details["error_type"] == "HTTPStatusError"
        assert details["provider_type"] == "invalid_request_error"
        assert details["provider_code"] == "missing_required_parameter"
        assert details["param"] == "messages[3].tool_call_id"
        assert details["message_class"] == "tool_call_response_mismatch"
        assert error.response.status_code == 400  # Preserve httpx compatibility.
        assert error.request.method == "POST"
        assert not is_retryable_infrastructure_error(error)
        safe = str(error) + repr(error) + json.dumps(details)
        assert "400" in str(error)
        assert KEY not in safe and PRIVATE not in safe and "provider.invalid" not in safe
    finally:
        await client.close()


@pytest.mark.parametrize(
    ("error", "message_class"),
    [
        ({"message": "Missing reasoning_content in the assistant message"},
         "missing_reasoning_content"),
        ({"message": "response_format is not supported"}, "unsupported_response_format"),
        ({"message": "Maximum context length exceeded"}, "context_length_exceeded"),
        ({"message": "Missing required parameter: model"}, "missing_required_parameter"),
        ({"message": "Unsupported parameter: temperature"}, "unsupported_parameter"),
        ({"message": "Tool arguments are not valid JSON"}, "invalid_tool_arguments"),
        ({"type": PRIVATE, "code": KEY, "param": PRIVATE, "message": PRIVATE},
         "unclassified"),
        ({"type": [], "code": {}, "param": 12, "message": [PRIVATE]}, "unclassified"),
    ],
)
@pytest.mark.asyncio
async def test_only_safe_categories_are_retained(error: dict, message_class: str) -> None:
    client = await mock_client(lambda request: httpx.Response(400, json={"error": error}))
    try:
        with pytest.raises(httpx.HTTPStatusError) as caught:
            await client.complete([])
        details = caught.value.http_diagnostics
        assert details["message_class"] == message_class
        assert "provider_type" not in details and "provider_code" not in details
        assert "param" not in details
        assert PRIVATE not in json.dumps(details) and KEY not in str(caught.value)
    finally:
        await client.close()


@pytest.mark.parametrize(
    ("body", "body_format"),
    [
        (f"<html>{PRIVATE} {KEY}</html>", "non_json"),
        ('{"error":', "non_json"),
        (json.dumps([PRIVATE, KEY]), "json_without_error_object"),
        (json.dumps({"error": PRIVATE}), "json_without_error_object"),
        (json.dumps({"error": {"message": PRIVATE * 3000}}), "omitted_oversized"),
    ],
    ids=["html", "invalid-json", "json-list", "string-error", "oversized"],
)
@pytest.mark.asyncio
async def test_nonstandard_and_large_error_bodies_fail_safely(
    body: str, body_format: str
) -> None:
    client = await mock_client(lambda request: httpx.Response(400, text=body))
    try:
        with pytest.raises(httpx.HTTPStatusError) as caught:
            await client.complete([])
        details = caught.value.http_diagnostics
        assert details["body_format"] == body_format
        assert details["message_class"] == "unclassified"
        assert len(json.dumps(details)) < 512
        assert PRIVATE not in json.dumps(details) and KEY not in str(caught.value)
    finally:
        await client.close()


@pytest.mark.parametrize(("status", "retryable"), [(400, False), (401, False), (429, True),
                                                        (500, True), (503, True)])
@pytest.mark.asyncio
async def test_status_type_and_retry_classification_are_unchanged(
    status: int, retryable: bool
) -> None:
    client = await mock_client(lambda request: httpx.Response(status, json=provider_error()))
    try:
        with pytest.raises(httpx.HTTPStatusError) as caught:
            await client.complete([])
        assert type(caught.value) is httpx.HTTPStatusError
        assert caught.value.http_diagnostics["status_code"] == status
        assert is_retryable_infrastructure_error(caught.value) is retryable
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_success_request_and_response_protocol_are_unchanged() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={
            "model": "resolved-offline-model",
            "choices": [{"message": {"content": "Synthetic answer"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
        })

    client = await mock_client(handler)
    messages = [{"role": "user", "content": "Synthetic request"}]
    tools = [{"type": "function", "function": {"name": "synthetic_tool"}}]
    try:
        response = await client.complete(
            messages, tools=tools, response_format={"type": "json_object"},
            max_tokens=123, temperature=0.25,
        )
        assert seen == [{
            "model": "offline-test", "messages": messages, "max_tokens": 123,
            "temperature": 0.25, "tools": tools, "tool_choice": "auto",
            "response_format": {"type": "json_object"},
        }]
        assert response.content == "Synthetic answer"
        assert response.model == "resolved-offline-model"
        assert response.usage["total_tokens"] == 6
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_tool_continuation_400_records_safe_diagnostics_and_does_not_retry(tmp_path) -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        seen.append(payload)
        if len(seen) == 1:
            content = json.dumps({"subtasks": [
                {
                    "subtask_id": "ST1", "description": "Inspect synthetic guideline evidence.",
                    "assigned_agent": "research_agent", "request_item_ids": ["RQ1"],
                },
                {
                    "subtask_id": "ST2", "description": "Summarize the upstream source limits.",
                    "assigned_agent": "consultation_agent", "request_item_ids": ["RQ2"],
                    "depends_on": ["ST1"],
                },
            ]})
            return httpx.Response(200, json={"choices": [{
                "message": {"content": content}, "finish_reason": "stop",
            }]})
        if len(seen) == 2:
            return httpx.Response(200, json={"choices": [{
                "message": {"content": "", "tool_calls": [{
                    "id": "offline-call-1", "type": "function",
                    "function": {"name": "clinical_guideline",
                                 "arguments": '{"query":"synthetic"}'},
                }]}, "finish_reason": "tool_calls",
            }]})
        assert len(seen) == 3
        assert [m["role"] for m in payload["messages"]] == [
            "system", "user", "assistant", "tool",
        ]
        assert payload["messages"][-1]["tool_call_id"] == "offline-call-1"
        assert "response_format" not in payload
        return httpx.Response(400, json=provider_error())

    client = await mock_client(handler)
    engine = NativeMedAgentEngine(
        RuntimeConfig(trace_dir=str(tmp_path)), llm=client,
        retrieval_backend=FakeRetrievalBackend([]),
    )
    try:
        result = await engine.analyze(
            "Synthetic fixture only.",
            "1. Use a guideline to inspect synthetic evidence.\n"
            "2. Based on step 1, summarize the source limitations.",
            "offline-http-diagnostic",
        )
    finally:
        await engine.close()
    assert len(seen) == 3
    assert result["status"] == "incomplete" and not result["user_request_complete"]
    assert result["final_answer"] == ""
    events = read_trace(tmp_path / result["run_id"])
    failure = next(e for e in events if e["event_type"] == "llm_response"
                   and e["payload"].get("error"))
    diagnostic = failure["payload"]["error"]["http"]
    assert failure["payload"]["error"]["type"] == "HTTPStatusError"
    assert diagnostic["status_code"] == 400
    assert diagnostic["param"] == "messages[3].tool_call_id"
    attempt = next(e for e in events if e["event_type"] == "worker_provider_attempt"
                   and e["payload"].get("error_type"))
    assert attempt["payload"]["http_error"] == diagnostic
    by_id = {e["event_id"]: e for e in events}
    failed_request = by_id[failure["parent_event_id"]]
    assert failed_request["event_type"] == "llm_request"
    assert by_id[failed_request["parent_event_id"]]["event_type"] == "tool_result"
    assert not any(e["event_type"] == "worker_infrastructure_retry_scheduled" for e in events)
    draft = next(e for e in events if e["event_type"] == "worker_draft")
    assert draft["payload"]["provider_attempt_count"] == 2
    assert draft["payload"]["infrastructure_retry_count"] == 0
    blocked = next(e for e in events if e["event_type"] == "worker_draft"
                   and e["payload"].get("worker_status") == "dependency_blocked")
    assert blocked["payload"]["provider_attempt_count"] == 0
    trace_text = (tmp_path / result["run_id"] / "trace.jsonl").read_text(encoding="utf-8")
    assert PRIVATE not in trace_text and KEY not in trace_text
