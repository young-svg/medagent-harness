"""Run the frozen A3 cases once, with a pre-request, concurrency-safe budget.

This module never assigns a semantic PASS. Human reviewers fill in the pending
rubric fields after inspecting the answer and Trace. Real execution is opt-in.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from pathlib import Path
from time import perf_counter
from typing import Any

from medagent.llm.client import LLMClient, LLMResponse, OpenAICompatibleLLM
from medagent.observability.replay import read_trace
from medagent.retrieval.backend import OffRetrievalBackend
from medagent.runtime.config import RuntimeConfig
from medagent.runtime.native_engine import NativeMedAgentEngine

CASES_PATH = Path(__file__).resolve().parents[2] / "docs" / "A3_COMPOSITE20_CASES.json"
FROZEN_SHA256 = "5647fa1c596934e7139377dd0a8791b760e17958cd518d1cd6913c0f18dc343c"
GROUPS = ("single", "independent_multi", "dependent_multi", "rag", "memory")
EXPECTED_IDS = tuple(
    f"{prefix}{number:02d}"
    for prefix in ("S", "I", "D", "R", "M")
    for number in range(1, 5)
)
REPO_ROOT = CASES_PATH.parents[1].resolve()


class RequestLimitExceeded(RuntimeError):
    """The next provider request was refused before the HTTP call."""


def load_frozen_cases(path: Path = CASES_PATH) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != FROZEN_SHA256:
        raise ValueError(f"A3 case SHA-256 mismatch: {digest}")
    payload = json.loads(raw)
    cases = payload["cases"]
    if [case["id"] for case in cases] != list(EXPECTED_IDS):
        raise ValueError("A3 case order or IDs differ from the frozen protocol")
    if [case["group"] for case in cases] != [group for group in GROUPS for _ in range(4)]:
        raise ValueError("A3 group order differs from the frozen protocol")
    required = (
        "id", "group", "session_id", "description", "question", "goal",
        "expected", "pass", "fail",
    )
    if any(
        not all(isinstance(case.get(key), str) and case[key].strip() for key in required)
        for case in cases
    ):
        raise ValueError("A3 case has an empty or invalid frozen field")
    if [(case["session_id"], case.get("round")) for case in cases[-4:]] != [
        ("a3-memory-A", 1), ("a3-memory-A", 2),
        ("a3-memory-B", 1), ("a3-memory-B", 2),
    ]:
        raise ValueError("A3 Memory session order differs from the frozen protocol")
    return payload, cases


def outside_repository(path: Path) -> Path:
    resolved = path.resolve()
    if resolved == REPO_ROOT or resolved.is_relative_to(REPO_ROOT):
        raise ValueError("A3 databases and outputs must be outside the repository")
    return resolved


def case_limit(group: str) -> int:
    if group not in GROUPS:
        raise ValueError(f"unknown A3 group: {group}")
    return 5 if group in {"single", "memory"} else 7


class RequestBudget:
    """Reserve a slot atomically before each LLMClient.complete/HTTP POST.

    The current OpenAICompatibleLLM issues exactly one HTTP POST per complete.
    A failed transport request still consumes its reserved slot. Blocked attempts
    are tracked separately and never sent to the provider.
    """

    def __init__(self, total_limit: int = 124) -> None:
        self.total_limit = total_limit
        self.total = 0
        self.by_case: dict[str, int] = {}
        self.blocked: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def reserve(self, case_id: str, group: str) -> None:
        async with self._lock:
            if self.total >= self.total_limit or self.by_case.get(case_id, 0) >= case_limit(group):
                self.blocked[case_id] = self.blocked.get(case_id, 0) + 1
                raise RequestLimitExceeded(f"A3 request limit reached for {case_id}")
            self.total += 1
            self.by_case[case_id] = self.by_case.get(case_id, 0) + 1


_ACTIVE_CASE: ContextVar[tuple[str, str] | None] = ContextVar("a3_active_case", default=None)


@contextmanager
def case_scope(case_id: str, group: str) -> Iterator[None]:
    token = _ACTIVE_CASE.set((case_id, group))
    try:
        yield
    finally:
        _ACTIVE_CASE.reset(token)


class BudgetedLLM:
    """Count Planner, Workers, retries, recovery, Synthesis and Presentation alike."""

    def __init__(self, client: LLMClient, budget: RequestBudget) -> None:
        self.client = client
        self.budget = budget
        self.model = getattr(client, "model", "unknown")
        self.temperature = getattr(client, "temperature", 0.0)
        self.max_tokens = getattr(client, "max_tokens", 1200)

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        active = _ACTIVE_CASE.get()
        if active is None:
            raise RuntimeError("A3 LLM call has no active case")
        await self.budget.reserve(*active)
        return await self.client.complete(
            messages, tools=tools, response_format=response_format,
            max_tokens=max_tokens, temperature=temperature,
        )

    async def close(self) -> None:
        await self.client.close()


def _trace_metrics(trace_path: Path | None) -> dict[str, Any]:
    if trace_path is None or not trace_path.is_file():
        return {
            "events": [], "usage": [], "token_totals": {}, "tool_calls": [],
            "retrieval_queries": [], "retrieval_results": [], "handoffs": [],
            "memory_events": [], "plan": None, "route": None,
            "worker_coverage": None, "final_coverage": None, "failure": None,
        }
    events = read_trace(trace_path)
    pick = lambda name: next(  # noqa: E731 - compact, read-only Trace extraction
        (event.get("payload") for event in reversed(events) if event["event_type"] == name), None
    )
    usage = [
        {"stage": event["event_type"], "usage": (event.get("payload") or {}).get("usage", {})}
        for event in events if event["event_type"] in {"llm_response", "presentation_transform"}
    ]
    totals = {
        key: sum(int(item["usage"].get(key) or 0) for item in usage)
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
    }
    return {
        "events": [event["event_type"] for event in events],
        "usage": usage,
        "token_totals": totals,
        "tool_calls": [
            event.get("payload") for event in events if event["event_type"] == "tool_call"
        ],
        "retrieval_queries": [
            event.get("payload") for event in events
            if event["event_type"] == "retrieval_query"
        ],
        "retrieval_results": [
            event.get("payload") for event in events
            if event["event_type"] == "retrieval_result"
        ],
        "handoffs": [
            event.get("payload") for event in events
            if event["event_type"] == "worker_handoff"
        ],
        "memory_events": [
            {"type": event["event_type"], "payload": event.get("payload")}
            for event in events if event["event_type"] in {"memory_read", "memory_write"}
        ],
        "plan": pick("plan_created"),
        "route": pick("route_selected"),
        "worker_coverage": pick("request_coverage"),
        "final_coverage": pick("final_request_coverage"),
        "failure": pick("error"),
    }


async def run_cases(
    cases: list[dict[str, Any]],
    *,
    output_dir: Path,
    off_engine: NativeMedAgentEngine,
    rag_engine: NativeMedAgentEngine,
    budget: RequestBudget,
) -> list[dict[str, Any]]:
    """Execute each supplied case once; production CLI always supplies all 20."""
    output_dir = outside_repository(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    results: list[dict[str, Any]] = []
    ledger = output_dir / "results.jsonl"
    for case in cases:
        case_id, group = case["id"], case["group"]
        engine = rag_engine if group == "rag" else off_engine
        trace_root = Path(engine.config.trace_dir)
        before = set(trace_root.iterdir()) if trace_root.is_dir() else set()
        started = perf_counter()
        result: dict[str, Any] | None = None
        error_type: str | None = None
        if budget.total >= budget.total_limit:
            error_type = "GlobalRequestLimitExceeded"
        else:
            try:
                with case_scope(case_id, group):
                    result = await engine.analyze(
                        case["description"], case["question"], case["session_id"]
                    )
            except Exception as error:  # each frozen case gets a durable failure record
                error_type = type(error).__name__
        new_runs = set(trace_root.iterdir()) - before if trace_root.is_dir() else set()
        run_id = result.get("run_id") if result else None
        trace_path = trace_root / run_id / "trace.jsonl" if run_id else None
        if trace_path is None and len(new_runs) == 1:
            trace_path = next(iter(new_runs)) / "trace.jsonl"
        if run_id is None and trace_path is not None and trace_path.is_file():
            run_id = trace_path.parent.name
        metrics = _trace_metrics(trace_path)
        blocked = budget.blocked.get(case_id, 0)
        record = {
            "case_id": case_id,
            "group": group,
            "case_sha256": hashlib.sha256(
                json.dumps(case, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest(),
            "session_id": case["session_id"],
            "round": case.get("round"),
            "run_id": run_id,
            "trace_path": str(trace_path) if trace_path and trace_path.is_file() else None,
            "status": (
                result.get("status") if result else
                "not_run" if error_type == "GlobalRequestLimitExceeded" else "failed"
            ),
            "user_request_complete": result.get("user_request_complete") if result else False,
            "final_answer": result.get("final_answer") if result else None,
            "requests_reserved": budget.by_case.get(case_id, 0),
            "requests_blocked": blocked,
            "elapsed_seconds": round(perf_counter() - started, 3),
            "error_type": error_type or ("RequestLimitExceeded" if blocked else None),
            **metrics,
            "semantic_review": {
                "status": "pending", "rubric_pass": case["pass"],
                "rubric_fail": case["fail"], "reviewer": None,
                "criterion_results": [], "reason": None,
            },
            "overall_verdict": "pending_review" if result and not blocked else "not_passed",
        }
        results.append(record)
        with ledger.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    return results


async def _run_real(args: argparse.Namespace) -> None:
    payload, cases = load_frozen_cases()
    output = outside_repository(args.output)
    database = outside_repository(args.milvus_db)
    if output.exists() or not database.exists():
        raise ValueError("output must be new and the A3 Milvus database must already exist")
    config = RuntimeConfig.from_env()
    if not config.llm_base_url or not config.llm_api_key or not config.llm_model:
        raise ValueError("real OpenAI-compatible API configuration is required")
    # The cached model is loaded without network access; schema and index are checked
    # before any real model client or output is created.
    from eval.a3.milvus_fixture import open_verified_backend

    rag_backend, metadata = await open_verified_backend(database, payload["rag_fixture"])
    trace_root = output / "traces"
    budget = RequestBudget()
    off_client = BudgetedLLM(
        OpenAICompatibleLLM(
            base_url=config.llm_base_url, api_key=config.llm_api_key,
            model=config.llm_model, temperature=config.llm_temperature,
            max_tokens=config.llm_max_tokens, timeout_seconds=config.llm_timeout_seconds,
        ), budget,
    )
    rag_client = BudgetedLLM(
        OpenAICompatibleLLM(
            base_url=config.llm_base_url, api_key=config.llm_api_key,
            model=config.llm_model, temperature=config.llm_temperature,
            max_tokens=config.llm_max_tokens, timeout_seconds=config.llm_timeout_seconds,
        ), budget,
    )
    off_engine = NativeMedAgentEngine(
        replace(config, retrieval_mode="off", trace_dir=str(trace_root)),
        llm=off_client, presentation_llm=off_client, retrieval_backend=OffRetrievalBackend(),
    )
    rag_engine = NativeMedAgentEngine(
        replace(config, retrieval_mode="milvus", trace_dir=str(trace_root)),
        llm=rag_client, presentation_llm=rag_client, retrieval_backend=rag_backend,
    )
    try:
        records = await run_cases(cases, output_dir=output, off_engine=off_engine,
                                  rag_engine=rag_engine, budget=budget)
        (output / "manifest.json").write_text(
            json.dumps({"case_sha256": FROZEN_SHA256, "model": config.llm_model,
                        "gateway_host": config.llm_base_url.split("/")[2],
                        "milvus": metadata, "requests_reserved": budget.total,
                        "case_count": len(records), "semantic_review": "pending"},
                       ensure_ascii=False, indent=2), encoding="utf-8",
        )
    finally:
        await off_engine.close()
        await rag_engine.close()
        rag_backend._client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the frozen A3 evaluation once")
    parser.add_argument("--execute-real", action="store_true", help="explicit paid-call opt-in")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--milvus-db", type=Path, required=True)
    args = parser.parse_args()
    if not args.execute_real:
        parser.error("--execute-real is required; no implicit provider calls")
    asyncio.run(_run_real(args))


if __name__ == "__main__":
    main()
