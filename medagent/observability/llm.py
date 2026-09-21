from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter
from typing import Any

from medagent.llm.client import LLMClient, LLMResponse
from medagent.observability.tracer import TraceRecorder


def _client_value(client: LLMClient, *names: str) -> Any:
    for name in names:
        value = getattr(client, name, None)
        if value is not None:
            return value
    return None


async def complete_with_trace(
    client: LLMClient,
    trace: TraceRecorder,
    *,
    stage: str,
    agent: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    response_format: dict[str, Any] | None = None,
    purpose: str,
    parent_event_id: str | None = None,
) -> tuple[LLMResponse, str]:
    """Execute one model call and persist its observable request/response boundary."""

    tool_choice = "auto" if tools else None
    requested_model = _client_value(client, "model", "model_name")
    request_id = trace.record(
        "llm_request",
        {
            "purpose": purpose,
            "requested_model": requested_model,
            "resolved_model": requested_model,
            "temperature": _client_value(client, "temperature"),
            "max_tokens": _client_value(client, "max_tokens"),
            "messages": messages,
            "tools": tools or [],
            "tool_choice": tool_choice,
            "response_format": response_format,
            "request_timestamp": datetime.now(UTC).isoformat(),
        },
        stage=stage,
        agent=agent,
        parent_event_id=parent_event_id,
    )
    started = perf_counter()
    try:
        response = await client.complete(
            messages,
            tools=tools,
            response_format=response_format,
        )
    except Exception as error:
        response_id = trace.record(
            "llm_response",
            {
                "purpose": purpose,
                "content": None,
                "tool_calls": [],
                "usage": {},
                "finish_reason": None,
                "resolved_model": None,
                "latency_ms": round((perf_counter() - started) * 1000, 2),
                "error": {"type": type(error).__name__, "message": str(error)},
            },
            stage=stage,
            agent=agent,
            parent_event_id=request_id,
        )
        del response_id
        raise
    response_id = trace.record(
        "llm_response",
        {
            "purpose": purpose,
            "content": response.content,
            "tool_calls": [
                {"id": call.id, "name": call.name, "arguments": call.arguments}
                for call in response.tool_calls
            ],
            "usage": response.usage,
            "finish_reason": response.finish_reason,
            "resolved_model": response.model,
            "latency_ms": round((perf_counter() - started) * 1000, 2),
            "error": None,
        },
        stage=stage,
        agent=agent,
        parent_event_id=request_id,
    )
    return response, response_id
