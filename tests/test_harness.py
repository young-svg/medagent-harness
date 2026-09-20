from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from api.main import app
from medagent.context.contract import AnswerContract, build_answer_contract
from medagent.context.evidence_ledger import EvidenceLedger, build_evidence_ledger
from medagent.guardrails.contract_checker import ContractChecker
from medagent.guardrails.sanitizer import sanitize_answer
from medagent.guardrails.stable_patch import apply_stable_patch
from medagent.memory.session import SessionMemory
from medagent.observability.replay import read_trace, replay, trace_summary
from medagent.planning.planner import Planner
from medagent.planning.router import Router
from medagent.presentation.adapter import PresentationAdapter
from medagent.retrieval.admission import admit_evidence
from medagent.retrieval.collection_router import CollectionRouter
from medagent.retrieval.evidence import EvidenceBundle, EvidenceItem
from medagent.retrieval.query_builder import QueryBuilder
from medagent.runtime.config import RuntimeConfig
from medagent.runtime.coordinator import Coordinator
from medagent.tools.registry import ToolRegistry


def test_contract_round_trip_and_validation() -> None:
    contract = build_answer_contract("What tests confirm the diagnosis?")
    restored = AnswerContract.from_dict(contract.to_dict())
    assert restored.intent == "diagnostic_workup"
    assert restored.requested_deliverables == ["FURTHER_TESTS"]


def test_evidence_ledger_round_trip_and_unique_ids() -> None:
    ledger = build_evidence_ledger("first fact; second fact")
    assert EvidenceLedger.from_dict(ledger.to_dict()).findings[1].finding_id == "E2"
    ledger.findings[1].finding_id = "E1"
    with pytest.raises(ValueError, match="unique"):
        ledger.validate()


def test_planner_parse_and_fallback() -> None:
    contract = build_answer_contract("diagnosis")
    parsed = Planner().plan(
        "diagnosis",
        contract,
        json.dumps({"subtasks": [{"description": "assess", "assigned_agent": "diagnostic_agent"}]}),
    )
    assert parsed.subtasks[0].assigned_agent == "diagnostic_agent"
    fallback = Planner().plan("diagnosis", contract, "not-json")
    assert fallback.fallback_reason == "planner_parse_fallback:JSONDecodeError"


def test_single_and_multi_routing() -> None:
    contract = build_answer_contract("diagnosis")
    single = Planner().plan("diagnosis", contract)
    assert Router().route(single).mode == "single"
    multi = Planner().plan("diagnosis with guideline evidence", contract)
    assert Router().route(multi).mode == "multi"


def test_query_builder_is_deterministic_and_deduplicates() -> None:
    query = QueryBuilder().build("same", "same", "FURTHER_TESTS", "extra")
    assert query == "same further tests extra"


def test_collection_router_freeze_v2_mapping() -> None:
    router = CollectionRouter()
    assert router.route("search_knowledge").collection == "medical_knowledge_v1"
    assert router.route("clinical_guideline").collection == "medical_knowledge"


def test_evidence_admission_and_compaction() -> None:
    items = [
        EvidenceItem("ev-1", "d1", None, None, None, None, None, "x" * 50, 0.7, 1),
        EvidenceItem("ev-2", "d2", None, None, None, None, None, "low", 0.2, 2),
    ]
    admit_evidence(items, 0.63, 5)
    bundle = EvidenceBundle("q", "c", items)
    assert [item.evidence_id for item in bundle.admitted_items] == ["ev-1"]
    assert len(bundle.compact(max_chars=10)[0]["text"]) == 10


def test_tool_visibility_and_execution_denial() -> None:
    registry = ToolRegistry()
    visible = {item["function"]["name"] for item in registry.schemas_for("diagnostic_agent")}
    assert "search_similar_cases" not in visible
    with pytest.raises(PermissionError):
        registry.execute("diagnostic_agent", "search_similar_cases", {})
    with pytest.raises(ValueError, match="EXPERIMENTAL_NOT_EXPOSED"):
        registry.register("search_similar_cases", lambda: None)


def test_tool_budget_is_enforced() -> None:
    registry = ToolRegistry(max_calls=2)
    registry.register("assess_risk", lambda symptoms: symptoms)
    assert registry.execute("diagnostic_agent", "assess_risk", {"symptoms": "one"})
    assert registry.execute("diagnostic_agent", "assess_risk", {"symptoms": "two"})
    with pytest.raises(RuntimeError, match="budget exhausted"):
        registry.execute("diagnostic_agent", "assess_risk", {"symptoms": "three"})


def test_session_memory_trim_dedup_and_current_priority() -> None:
    memory = SessionMemory(recent_limit=2)
    memory.add("s", "user", "one")
    memory.add("s", "user", "one")
    memory.add("s", "assistant", "two")
    memory.add("s", "user", "three")
    assert memory.context("s", "current")[-1]["content"] == "current"
    assert len(memory.context("s", "current")) == 3


def test_checker_detection_is_not_edit_permission() -> None:
    result = ContractChecker().check("short answer", build_answer_contract("diagnosis"))
    assert not result.passed
    assert not result.edits_allowed


def test_stable_patch_preserves_ambiguous_answer() -> None:
    original = "No stable marker"
    result = apply_stable_patch(original, {"S1": "replacement"})
    assert not result.applied and result.answer == original
    assert apply_stable_patch("[[S1]]", {"S1": "safe"}).answer == "safe"


def test_sanitizer_removes_private_reasoning_markers() -> None:
    assert sanitize_answer("Visible\n<analysis>secret</analysis>") == "Visible"


def test_trace_parent_lifecycle_and_replay() -> None:
    trace_root = Path("runs/tests") / str(uuid4())
    config = RuntimeConfig(trace_dir=str(trace_root))
    result = Coordinator(config=config).analyze("SYNTHETIC EXAMPLE; fatigue", "assess", "s")
    run_dir = trace_root / result["run_id"]
    summary = trace_summary(run_dir)
    events = read_trace(run_dir)
    assert summary["events"][0] == "run_start"
    assert summary["events"][-1] == "run_end"
    assert events[0]["parent_id"] is None
    assert all(item["parent_id"] == result["run_id"] for item in events[1:])
    assert replay(run_dir)["final_answer"]


def test_presentation_adapter_never_invents_source() -> None:
    item = EvidenceItem("ev", "d", None, None, None, None, None, "text", 0.8, 1, admitted=True)
    response = PresentationAdapter().adapt(
        "Professional answer",
        build_answer_contract("diagnosis"),
        build_evidence_ledger("fact"),
        EvidenceBundle("q", "c", [item]),
        {},
    )
    assert response.evidence_cards[0].source == "Source metadata unavailable"


def test_api_schema_health_and_analysis(monkeypatch: pytest.MonkeyPatch) -> None:
    trace_root = Path("runs/tests") / str(uuid4())
    monkeypatch.setattr(
        "api.main._coordinator", Coordinator(RuntimeConfig(trace_dir=str(trace_root)))
    )
    client = TestClient(app)
    assert client.get("/health").json()["status"] == "ok"
    response = client.post(
        "/api/analyze",
        json={
            "description": "SYNTHETIC EXAMPLE; fatigue",
            "question": "What should be assessed?",
            "session_id": "api-test",
        },
    )
    assert response.status_code == 200
    assert set(response.json()) == {"answer", "run_id", "trace_summary"}
    assert (
        client.post(
            "/api/analyze", json={"description": "", "question": "q", "session_id": "s"}
        ).status_code
        == 422
    )
