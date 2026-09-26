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
    max_tokens: int | None = None,
    temperature: float | None = None,
    planner_max_tokens: int | None = None,
    attempt_index: int | None = None,
    recovery_type: str | None = None,
) -> tuple[LLMResponse, str]:
    """Execute one model call and persist its observable request/response boundary."""

    tool_choice = "auto" if tools else None
    requested_model = _client_value(client, "model", "model_name")
    effective_max_tokens = max_tokens if max_tokens is not None else _client_value(
        client, "max_tokens"
    )
    planner_metadata = {
        "planner_max_tokens": planner_max_tokens,
        "attempt_index": attempt_index,
        "recovery_type": recovery_type,
    }
    request_id = trace.record(
        "llm_request",
        {
            "purpose": purpose,
            "requested_model": requested_model,
            "resolved_model": requested_model,
            "temperature": (
                temperature
                if temperature is not None
                else _client_value(client, "temperature")
            ),
            "max_tokens": effective_max_tokens,
            "messages": messages,
            "tools": tools or [],
            "tool_choice": tool_choice,
            "response_format": response_format,
            "request_timestamp": datetime.now(UTC).isoformat(),
            **planner_metadata,
        },
        stage=stage,
        agent=agent,
        parent_event_id=parent_event_id,
    )
    started = perf_counter()
    try:
        completion_kwargs = {"tools": tools, "response_format": response_format}
        if max_tokens is not None:
            completion_kwargs["max_tokens"] = max_tokens
        if temperature is not None:
            completion_kwargs["temperature"] = temperature
        response = await client.complete(messages, **completion_kwargs)
    except Exception as error:
        response_id = trace.record(
            "llm_response",
            {
                "purpose": purpose,
                "content": None,
                "content_length": 0,
                "tool_calls": [],
                "tool_call_count": 0,
                "usage": {},
                "finish_reason": None,
                "resolved_model": None,
                "max_tokens": effective_max_tokens,
                "latency_ms": round((perf_counter() - started) * 1000, 2),
                "error": {"type": type(error).__name__, "message": str(error)},
                **planner_metadata,
            },
            stage=stage,
            agent=agent,
            parent_event_id=request_id,
        )
        del response_id
        raise
    usage = dict(response.usage)
    completion_details = usage.get("completion_tokens_details") or {}
    if "reasoning_tokens" in completion_details:
        usage.setdefault("reasoning_tokens", completion_details["reasoning_tokens"])
    response_id = trace.record(
        "llm_response",
        {
            "purpose": purpose,
            "content": response.content,
            "content_length": len(response.content),
            "tool_calls": [
                {"id": call.id, "name": call.name, "arguments": call.arguments}
                for call in response.tool_calls
            ],
            "tool_call_count": len(response.tool_calls),
            "usage": usage,
            "finish_reason": response.finish_reason,
            "resolved_model": response.model,
            "max_tokens": effective_max_tokens,
            "latency_ms": round((perf_counter() - started) * 1000, 2),
            "error": None,
            **planner_metadata,
        },
        stage=stage,
        agent=agent,
        parent_event_id=request_id,
    )
    return response, response_id
