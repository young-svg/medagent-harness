from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from time import perf_counter
from typing import Any

import httpx

from medagent.llm.client import LLMClient, LLMResponse
from medagent.observability.llm import complete_with_trace
from medagent.observability.tracer import TraceRecorder, redact_trace_value

CompletionCheck = Callable[[LLMResponse], bool]


@dataclass(frozen=True, slots=True)
class GenerationPolicy:
    """Stage-specific output ceiling and bounded length-recovery policy."""

    max_tokens: int
    max_length_recoveries: int = 1
    recovery_type: str = "length_completion_recovery"

    def __post_init__(self) -> None:
        if self.max_tokens <= 0:
            raise ValueError("generation max_tokens must be positive")
        if self.max_length_recoveries not in {0, 1}:
            raise ValueError("generation max_length_recoveries must be 0 or 1")


@dataclass(slots=True)
class GenerationResult:
    """Observable outcome of one logical generation, including all attempts."""

    response: LLMResponse
    response_id: str | None
    generation_status: str
    length_recovery_count: int
    attempt_count: int
    usage: dict[str, int]
    latency_ms: float
    infrastructure_retry_count: int
    provider_attempt_count: int


def is_retryable_infrastructure_error(error: BaseException) -> bool:
    """Classify only transient transport and provider-capacity failures as retryable."""

    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (httpx.TransportError, ConnectionError, TimeoutError)):
            return True
        if type(current).__module__.split(".", 1)[0] == "httpcore":
            return True
        if type(current).__module__.split(".", 1)[0] == "anyio" and type(
            current
        ).__name__ in {"EndOfStream", "BrokenResourceError", "ClosedResourceError"}:
            return True
        if isinstance(current, httpx.HTTPStatusError):
            status_code = current.response.status_code
            return status_code == 429 or 500 <= status_code <= 599
        status_code = getattr(current, "status_code", None)
        if status_code is None:
            response = getattr(current, "response", None)
            status_code = getattr(response, "status_code", None)
        if isinstance(status_code, int):
            return status_code == 429 or 500 <= status_code <= 599
        message = str(current).casefold()
        if any(
            marker in message
            for marker in ("connection reset", "connection aborted", "end of stream")
        ):
            return True
        current = current.__cause__ or current.__context__
    return False


def _sanitized_error_message(error: BaseException) -> str:
    message = str(redact_trace_value(str(error))).replace("\r", " ").replace("\n", " ")
    return message[:500]


def _empty_usage() -> dict[str, int]:
    return {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
    }


def _add_usage(total: dict[str, int], usage: dict[str, Any]) -> None:
    completion_details = usage.get("completion_tokens_details") or {}
    reasoning_tokens = usage.get("reasoning_tokens")
    if reasoning_tokens is None:
        reasoning_tokens = completion_details.get("reasoning_tokens", 0)
    total["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
    total["completion_tokens"] += int(usage.get("completion_tokens") or 0)
    total["reasoning_tokens"] += int(reasoning_tokens or 0)
    total["total_tokens"] += int(usage.get("total_tokens") or 0)


async def run_with_length_recovery(
    client: LLMClient,
    *,
    policy: GenerationPolicy,
    stage: str,
    agent: str,
    messages: list[dict[str, Any]],
    purpose: str,
    is_complete: CompletionCheck,
    trace: TraceRecorder | None = None,
    tools: list[dict[str, Any]] | None = None,
    response_format: dict[str, Any] | None = None,
    parent_event_id: str | None = None,
    max_infrastructure_retries: int = 0,
    worker_id: str | None = None,
    subtask_id: str | None = None,
    provider_attempt_index_offset: int = 0,
) -> GenerationResult:
    """Run one generation plus at most one same-input recovery after truncation."""

    started = perf_counter()
    usage = _empty_usage()
    recovery_count = 0
    infrastructure_retry_count = 0
    attempt_count = 0
    last_response = LLMResponse()
    last_response_id: str | None = None
    pending_infrastructure_retry = False

    def finish(status: str) -> GenerationResult:
        latency_ms = round((perf_counter() - started) * 1000, 2)
        if trace is not None:
            trace.record(
                "generation_summary",
                {
                    "purpose": purpose,
                    "generation_status": status,
                    "length_recovery_count": recovery_count,
                    "attempt_count": attempt_count,
                    "usage": usage,
                    "latency_ms": latency_ms,
                },
                stage=stage,
                agent=agent,
                parent_event_id=last_response_id or parent_event_id,
            )
        return GenerationResult(
            last_response,
            last_response_id,
            status,
            recovery_count,
            attempt_count,
            usage,
            latency_ms,
            infrastructure_retry_count,
            attempt_count,
        )

    while True:
        attempt_count += 1
        recovery_type = (
            "infrastructure_retry"
            if pending_infrastructure_retry
            else policy.recovery_type if recovery_count else None
        )
        attempt_started = perf_counter()
        try:
            if trace is not None:
                response, response_id = await complete_with_trace(
                    client,
                    trace,
                    stage=stage,
                    agent=agent,
                    messages=messages,
                    tools=tools,
                    response_format=response_format,
                    purpose=purpose,
                    parent_event_id=parent_event_id,
                    max_tokens=policy.max_tokens,
                    planner_max_tokens=(policy.max_tokens if stage == "planning" else None),
                    attempt_index=attempt_count,
                    recovery_type=recovery_type,
                )
            else:
                response = await client.complete(
                    messages,
                    tools=tools,
                    response_format=response_format,
                    max_tokens=policy.max_tokens,
                )
                response_id = None
        except Exception as error:
            attempt_latency_ms = round((perf_counter() - attempt_started) * 1000, 2)
            retryable = is_retryable_infrastructure_error(error)
            if trace is not None and worker_id is not None and subtask_id is not None:
                trace.record(
                    "worker_provider_attempt",
                    {
                        "worker_id": worker_id,
                        "subtask_id": subtask_id,
                        "attempt_index": provider_attempt_index_offset + attempt_count,
                        "retry_type": recovery_type,
                        "error_type": type(error).__name__,
                        "error_message_sanitized": _sanitized_error_message(error),
                        "provider": type(client).__name__,
                        "model": getattr(client, "model", None),
                        "latency_ms": attempt_latency_ms,
                        "outcome": "error",
                    },
                    stage=stage,
                    agent=agent,
                    parent_event_id=parent_event_id,
                )
            if retryable and infrastructure_retry_count < max_infrastructure_retries:
                infrastructure_retry_count += 1
                pending_infrastructure_retry = True
                continue
            for name, value in (
                ("infrastructure_retry_count", infrastructure_retry_count),
                ("provider_attempt_count", attempt_count),
            ):
                try:
                    setattr(error, name, value)
                except (AttributeError, TypeError):
                    pass
            finish("provider_error")
            raise

        attempt_latency_ms = round((perf_counter() - attempt_started) * 1000, 2)
        if trace is not None and worker_id is not None and subtask_id is not None:
            trace.record(
                "worker_provider_attempt",
                {
                    "worker_id": worker_id,
                    "subtask_id": subtask_id,
                    "attempt_index": provider_attempt_index_offset + attempt_count,
                    "retry_type": recovery_type,
                    "error_type": None,
                    "error_message_sanitized": None,
                    "provider": type(client).__name__,
                    "model": response.model or getattr(client, "model", None),
                    "latency_ms": attempt_latency_ms,
                    "outcome": "success",
                },
                stage=stage,
                agent=agent,
                parent_event_id=response_id,
            )
        pending_infrastructure_retry = False
        last_response = response
        last_response_id = response_id
        _add_usage(usage, response.usage)
        complete = is_complete(response)
        if complete:
            status = (
                "completed_after_length_recovery"
                if recovery_count
                else "completed_first_attempt"
            )
            return finish(status)
        if response.finish_reason != "length" and recovery_count == 0:
            return finish("completed_first_attempt")
        if recovery_count >= policy.max_length_recoveries:
            return finish("length_exhausted")
        recovery_count += 1
