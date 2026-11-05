"""Reserve before real attempts. Missing usage is unknown, including failures."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import Counter
from contextvars import ContextVar
from time import perf_counter
from typing import Any

from medagent.runtime.agent_loop import WorkerBudgetExceeded

tool_scope: ContextVar[tuple[str, str] | None] = ContextVar("w7_tool_scope", default=None)
http_attempt: ContextVar[dict | None] = ContextVar("w7_http_attempt", default=None)


class RunBudgetExceeded(WorkerBudgetExceeded):
    pass


class BatchStopped(WorkerBudgetExceeded):
    pass


class BatchBudget:
    """One shared, latched admission gate for all runs in a live batch."""

    def __init__(self, limit):
        if type(limit) is not int or limit < 1:
            raise ValueError("invalid_batch_limit")
        self.limit, self.attempts, self.stop_reason = limit, 0, None
        self.lock = asyncio.Lock()

    def stop(self, reason):
        if self.stop_reason is None:
            self.stop_reason = reason

    async def reserve(self):
        async with self.lock:
            if self.stop_reason:
                raise BatchStopped(self.stop_reason)
            if self.attempts >= self.limit:
                self.stop("batch_model_budget_exhausted")
                raise BatchStopped(self.stop_reason)
            self.attempts += 1
            return self.attempts


def safe_usage(usage):
    keys = {
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "reasoning_tokens",
        "cached_tokens",
        "prompt_cache_hit_tokens",
        "prompt_cache_miss_tokens",
    }
    result = {k: v for k, v in usage.items() if k in keys and type(v) is int and v >= 0}
    for key in ("prompt_tokens_details", "completion_tokens_details"):
        if isinstance(usage.get(key), dict):
            nested = safe_usage(usage[key])
            if nested:
                result[key] = nested
    return result


def model_stage(messages: list[dict], validation: bool = False) -> str:
    if validation:
        return "acceptance"
    system = messages[0]["content"]
    # Pinned trusted runtime prompts, never user text. Unknown paths fail closed.
    if system.startswith("You are the public MedAgent centralized planner"):
        return "planning"
    if system.startswith("Role:") or system.startswith("You are a serialization repair step."):
        return "worker"
    if system.startswith("Return exactly one JSON object with an answers array."):
        return "synthesis"
    raise ValueError("unrecognized_evaluation_model_boundary")


class RunBudget:
    def __init__(self, *, worker_limit=8, tool_limit=8, model_limit=16, batch=None):
        for value in (worker_limit, tool_limit, model_limit):
            if type(value) is not int or value < 0:
                raise ValueError("budget_requires_nonnegative_integers")
        self.worker_limit, self.tool_limit, self.model_limit = worker_limit, tool_limit, model_limit
        self.batch = batch
        self.started = perf_counter()
        self.lock = asyncio.Lock()
        self.models: list[dict[str, Any]] = []
        self.tools: list[dict[str, Any]] = []
        self.denied: list[str] = []
        self.worker_tasks: set[asyncio.Task] = set()
        self.worker_contexts: dict[asyncio.Task, tuple[str, list[str]]] = {}
        self.peak_workers = 0

    async def reserve_model(self, stage, *, task_id=None, request_ids=(), tools=()):
        async with self.lock:
            reason = (
                "run_model_budget_exhausted"
                if len(self.models) >= self.model_limit
                else "worker_model_budget_exhausted"
                if stage == "worker"
                and sum(m["stage"] == "worker" for m in self.models) >= self.worker_limit
                else self.denied[0]
                if self.denied
                else None
            )
            if reason:
                self.denied.append(reason)
                if self.batch:
                    self.batch.stop(reason)
                raise RunBudgetExceeded(reason)
            batch_attempt = await self.batch.reserve() if self.batch else None
            row = {
                "attempt": len(self.models) + 1,
                "stage": stage,
                "task_id": task_id,
                "request_item_ids": list(request_ids),
                "tool_schema_names": list(tools),
                "status": "pending",
                "input_tokens": "UNKNOWN",
                "output_tokens": "UNKNOWN",
                "usage_origin": "UNKNOWN",
                "provider_usage": "UNKNOWN",
                "http_status": "UNKNOWN",
                "resolved_model": "UNKNOWN",
                "started": perf_counter(),
            }
            row["start_ms"] = round((row["started"] - self.started) * 1000, 3)
            self.models.append(row)
            if batch_attempt is not None:
                row["batch_attempt"] = batch_attempt
            if stage == "worker":
                self.worker_tasks = {t for t in self.worker_tasks if not t.done()}
                self.worker_tasks.add(asyncio.current_task())
                self.peak_workers = max(self.peak_workers, len(self.worker_tasks))
            return row

    async def reserve_tool(self, name, context):
        async with self.lock:
            if self.batch and self.batch.stop_reason:
                raise BatchStopped(self.batch.stop_reason)
            if self.denied or len(self.tools) >= self.tool_limit:
                reason = self.denied[0] if self.denied else "run_tool_budget_exhausted"
                self.denied.append(reason)
                if self.batch:
                    self.batch.stop(reason)
                raise RunBudgetExceeded(reason)
            row = {
                "attempt": len(self.tools) + 1,
                "name": name,
                "task_id": context.subtask_id if context else None,
                "status": "pending",
                "handler_executions": 0,
            }
            self.tools.append(row)
            return row

    def summary(self):
        def total(key):
            return (
                sum(m[key] for m in self.models)
                if self.models and all(type(m[key]) is int for m in self.models)
                else "UNKNOWN"
            )

        return {
            "model_attempts": len(self.models),
            "by_stage": dict(Counter(m["stage"] for m in self.models)),
            "successful_requests": sum(m["status"] == "success" for m in self.models),
            "failed_requests": sum(m["status"] == "failed" for m in self.models),
            "unknown_usage_requests": sum(
                m["input_tokens"] == "UNKNOWN" or m["output_tokens"] == "UNKNOWN"
                for m in self.models
            ),
            "input_tokens": total("input_tokens"),
            "output_tokens": total("output_tokens"),
            "tool_attempts": len(self.tools),
            "handler_executions": sum(t["handler_executions"] for t in self.tools),
            "peak_worker_contexts": self.peak_workers,
            "limits": {
                "worker_models": self.worker_limit,
                "tools": self.tool_limit,
                "all_models": self.model_limit,
            },
            "budget_exhausted": bool(self.denied),
            "denied": self.denied,
        }


class MeteredLLM:
    def __init__(
        self,
        client,
        budget: RunBudget,
        *,
        validation=False,
        usage_origin="UNKNOWN",
        stage_override=None,
        allow_recovery=True,
    ):
        self.client, self.budget, self.validation = client, budget, validation
        self.usage_origin = usage_origin
        if stage_override not in {None, "probe_json"}:
            raise ValueError("invalid_stage_override")
        self.stage_override, self.allow_recovery = stage_override, allow_recovery
        self.worker_payloads = []

    def __getattr__(self, key):
        # Keep the existing Native drift guard connected to the actual injected client.
        if key in {"model", "temperature", "max_tokens", "base_url"}:
            return getattr(self.client, key, None)
        raise AttributeError(key)

    async def complete(self, messages, **kwargs):
        if not self.allow_recovery and messages[0]["content"].startswith(
            "You are a serialization repair step."
        ):
            self.budget.batch.stop("protocol_repair_not_authorized")
            raise BatchStopped("protocol_repair_not_authorized")
        stage = self.stage_override or model_stage(messages, self.validation)
        task_id, request_ids = None, []
        if stage == "worker":
            # Recovery still counts even when its payload has no original task.
            try:
                subtask = json.loads(messages[1]["content"])["subtask"]
                task_id, request_ids = subtask["subtask_id"], subtask["request_item_ids"]
                self.budget.worker_contexts[asyncio.current_task()] = (task_id, request_ids)
                self.worker_payloads.append(json.loads(messages[1]["content"]))
            except (ValueError, KeyError, TypeError):
                task_id, request_ids = self.budget.worker_contexts.get(
                    asyncio.current_task(), (None, [])
                )
        row = await self.budget.reserve_model(
            stage,
            task_id=task_id,
            request_ids=request_ids,
            tools=[t["function"]["name"] for t in kwargs.get("tools") or []],
        )
        row["tool_schema_sha256"] = hashlib.sha256(
            json.dumps(
                kwargs.get("tools") or [], sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
        ).hexdigest()
        row["effective_parameters"] = {
            "model": self.model,
            "max_output_tokens": kwargs.get("max_tokens")
            if kwargs.get("max_tokens") is not None
            else self.max_tokens,
            "temperature": kwargs.get("temperature")
            if kwargs.get("temperature") is not None
            else self.temperature,
        }
        token = http_attempt.set(row)
        try:
            response = await self.client.complete(messages, **kwargs)
            row["status"] = "success"
            row["resolved_model"] = response.model or "UNKNOWN"
            row["provider_usage"] = safe_usage(response.usage) or "UNKNOWN"
            row["returned_tool_calls"] = len(response.tool_calls)
            row["finish_reason"] = response.finish_reason
            for target, source in (
                ("input_tokens", "prompt_tokens"),
                ("output_tokens", "completion_tokens"),
            ):
                value = response.usage.get(source)
                if type(value) is int and value >= 0:
                    row[target] = value
            if row["provider_usage"] != "UNKNOWN":
                row["usage_origin"] = self.usage_origin
            return response
        except BaseException as error:
            row.update(status="failed", error_type=type(error).__name__)
            if diagnostics := getattr(error, "http_diagnostics", None):
                row["http_error"] = diagnostics
                row["http_status"] = diagnostics["status_code"]
            if self.budget.batch:
                # No live retries: latch before any other task can start a request.
                self.budget.batch.stop("provider_request_failed")
            raise
        finally:
            http_attempt.reset(token)
            row["latency_ms"] = round((perf_counter() - row.pop("started")) * 1000, 3)
            row["end_ms"] = round((perf_counter() - self.budget.started) * 1000, 3)

    async def close(self):
        await self.client.close()


class MeteredTools:
    """Proxy the original registry; attempts and actual Handler entries differ."""

    def __init__(self, registry, budget):
        self.registry, self.budget = registry, budget
        self.active: ContextVar[dict | None] = ContextVar("w7_handler_attempt", default=None)
        # Register wrappers via the existing API, keeping contextual dispatch unchanged.
        for name, handler in list(registry._handlers.items()):

            async def counted(*args, _handler=handler, **kwargs):
                import inspect

                row = self.active.get()
                if row is None:
                    raise RuntimeError("handler_outside_metered_attempt")
                row["handler_executions"] += 1
                result = _handler(*args, **kwargs)
                return await result if inspect.isawaitable(result) else result

            registry.register(name, counted, with_context=name in registry._contextual_handlers)

    def __getattr__(self, name):
        return getattr(self.registry, name)

    async def execute(self, worker, name, arguments, *, context=None):
        row = await self.budget.reserve_tool(name, context)
        active_token = self.active.set(row)
        fallback = (
            context.request_items[0].id
            if context is not None and len(context.request_items) == 1
            else ""
        )
        scope_token = tool_scope.set((arguments.get("request_item_id", fallback), name))
        try:
            value = await self.registry.execute(worker, name, arguments, context=context)
            row["status"] = "success"
            return value
        except BaseException as error:
            row.update(status="failed", error_type=type(error).__name__)
            if self.budget.batch:
                self.budget.batch.stop("tool_execution_failed")
            raise
        finally:
            self.active.reset(active_token)
            tool_scope.reset(scope_token)
