from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from eval.a3.milvus_fixture import _check_fixture
from eval.a3.runner import (
    BudgetedLLM,
    RequestBudget,
    RequestLimitExceeded,
    case_scope,
    load_frozen_cases,
    outside_repository,
    run_cases,
)
from medagent.llm.client import LLMResponse, OpenAICompatibleLLM
from medagent.llm.fakes import ScriptedLLM
from medagent.runtime.config import RuntimeConfig
from medagent.runtime.native_engine import NativeMedAgentEngine


def test_frozen_hash_order_groups_memory_and_fixture(tmp_path: Path) -> None:
    payload, cases = load_frozen_cases()
    assert len(cases) == 20
    assert [case["id"] for case in cases] == [
        f"{prefix}{number:02d}" for prefix in "SIDRM" for number in range(1, 5)
    ]
    assert [(case["session_id"], case.get("round")) for case in cases[-4:]] == [
        ("a3-memory-A", 1), ("a3-memory-A", 2),
        ("a3-memory-B", 1), ("a3-memory-B", 2),
    ]
    _check_fixture(payload["rag_fixture"])
    altered = tmp_path / "changed.json"
    altered.write_bytes(json.dumps(payload, ensure_ascii=False).encode())
    with pytest.raises(ValueError, match="SHA-256"):
        load_frozen_cases(altered)
    with pytest.raises(ValueError, match="outside"):
        outside_repository(Path(__file__).resolve().parents[1] / "runs")


@dataclass
class CountingClient:
    model: str = "offline-counting"
    max_tokens: int = 1200
    temperature: float = 0.0
    calls: int = 0

    async def complete(self, messages: list[dict[str, Any]], **kwargs: Any) -> LLMResponse:
        self.calls += 1
        return LLMResponse("offline", usage={"total_tokens": 2})

    async def close(self) -> None:
        return None


@pytest.mark.parametrize("group,allowed", [("single", 5), ("memory", 5), ("rag", 7),
                                             ("independent_multi", 7), ("dependent_multi", 7)])
def test_concurrent_calls_are_reserved_before_client(group: str, allowed: int) -> None:
    async def scenario() -> tuple[int, int, int]:
        budget = RequestBudget()
        client = CountingClient()
        wrapper = BudgetedLLM(client, budget)

        async def request() -> bool:
            try:
                with case_scope("X01", group):
                    await wrapper.complete([{"role": "user", "content": "synthetic"}])
                return True
            except RequestLimitExceeded:
                return False

        outcomes = await asyncio.gather(*(request() for _ in range(30)))
        return sum(outcomes), client.calls, budget.blocked["X01"]

    assert asyncio.run(scenario()) == (allowed, allowed, 30 - allowed)


def test_global_limit_and_failed_transport_count() -> None:
    async def scenario() -> None:
        budget = RequestBudget(total_limit=2)
        client = CountingClient()
        wrapper = BudgetedLLM(client, budget)
        with pytest.raises(RuntimeError, match="no active case"):
            await wrapper.complete([])
        with case_scope("S01", "single"):
            await wrapper.complete([])
            await wrapper.complete([])
            with pytest.raises(RequestLimitExceeded):
                await wrapper.complete([])
        assert client.calls == budget.total == 2

    asyncio.run(scenario())


def test_real_client_http_boundary_counts_failures_and_blocks_next_post() -> None:
    async def scenario() -> None:
        seen: list[httpx.Request] = []

        def transport(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            raise httpx.ReadTimeout("offline injected timeout")

        client = OpenAICompatibleLLM(
            base_url="https://example.invalid/v1", api_key="synthetic-test-only",
            model="offline-test",
        )
        await client._client.aclose()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        budget = RequestBudget(total_limit=1)
        wrapper = BudgetedLLM(client, budget)
        try:
            with case_scope("S01", "single"):
                with pytest.raises(httpx.ReadTimeout):
                    await wrapper.complete([{"role": "user", "content": "synthetic"}])
                with pytest.raises(RequestLimitExceeded):
                    await wrapper.complete([{"role": "user", "content": "synthetic"}])
            assert len(seen) == budget.total == 1
        finally:
            await wrapper.close()

    asyncio.run(scenario())


def test_total_124_slots_are_shared_across_cases() -> None:
    async def scenario() -> None:
        budget = RequestBudget()
        _, cases = load_frozen_cases()
        for case in cases:
            for _ in range(5 if case["group"] in {"single", "memory"} else 7):
                await budget.reserve(case["id"], case["group"])
        assert budget.total == 124
        with pytest.raises(RequestLimitExceeded):
            await budget.reserve("S01", "single")
        assert budget.total == 124

    asyncio.run(scenario())


class RecordingEngine:
    def __init__(self, trace_dir: Path, *, fail_case: str | None = None) -> None:
        self.config = SimpleNamespace(trace_dir=str(trace_dir))
        self.calls: list[tuple[str, str, str]] = []
        self.fail_case = fail_case

    async def analyze(self, description: str, question: str, session_id: str) -> dict[str, Any]:
        self.calls.append((description, question, session_id))
        if self.fail_case and self.fail_case in session_id:
            raise ValueError("offline injected failure")
        return {"status": "completed", "user_request_complete": True,
                "final_answer": "mock answer", "run_id": None}


def test_all_cases_order_memory_and_durable_pending_records(tmp_path: Path) -> None:
    _, cases = load_frozen_cases()
    off = RecordingEngine(tmp_path / "off", fail_case="a3-s02")
    rag = RecordingEngine(tmp_path / "rag")
    budget = RequestBudget()
    records = asyncio.run(run_cases(cases, output_dir=tmp_path / "results",
                                    off_engine=off, rag_engine=rag, budget=budget))
    assert [row["case_id"] for row in records] == [case["id"] for case in cases]
    assert [item[2] for item in off.calls[-4:]] == [
        "a3-memory-A", "a3-memory-A", "a3-memory-B", "a3-memory-B"
    ]
    assert [item[2] for item in rag.calls] == [f"a3-r{number:02d}" for number in range(1, 5)]
    assert records[1]["status"] == "failed"
    assert records[1]["error_type"] == "ValueError"
    assert records[0]["overall_verdict"] == "pending_review"
    assert all(item["semantic_review"]["status"] == "pending" for item in records)
    lines = (tmp_path / "results" / "results.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 20
    assert json.loads(lines[1])["status"] == "failed"
    with pytest.raises(FileExistsError):
        asyncio.run(run_cases(cases, output_dir=tmp_path / "results",
                              off_engine=off, rag_engine=rag, budget=budget))


def test_scripted_real_engine_smoke_keeps_semantics_pending(tmp_path: Path) -> None:
    _, cases = load_frozen_cases()
    client = ScriptedLLM([
        {"subtasks": [{"subtask_id": "s1", "description": "Summarize facts",
                       "assigned_agent": "diagnostic_agent", "request_item_ids": ["RQ1"]}]},
        {"answers": [{"request_item_id": "RQ1", "answer":
                      "蓝桥，体温36.7℃；未提供其他病史，不能据此诊断。"}]},
        "Presentation formatting offline response.",
    ])
    budget = RequestBudget()
    wrapper = BudgetedLLM(client, budget)
    engine = NativeMedAgentEngine(
        RuntimeConfig(trace_dir=str(tmp_path / "trace")),
        llm=wrapper, presentation_llm=wrapper,
    )

    async def scenario() -> list[dict[str, Any]]:
        try:
            return await run_cases(cases[:1], output_dir=tmp_path / "result",
                                   off_engine=engine, rag_engine=engine, budget=budget)
        finally:
            await engine.close()

    records = asyncio.run(scenario())
    assert records[0]["status"] == "completed"
    assert records[0]["user_request_complete"] is True
    assert records[0]["requests_reserved"] == len(client.requests) == 3
    assert records[0]["trace_path"] is not None
    assert records[0]["final_coverage"]["user_request_complete"] is True
    assert records[0]["overall_verdict"] == "pending_review"
