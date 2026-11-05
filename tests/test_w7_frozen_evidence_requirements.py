"""Declared evidence follows the real frozen Native/Worker/receipt path offline."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import fields, replace
from pathlib import Path

import pytest

from medagent.context.contract import AnswerContract
from medagent.context.request_spec import RequestSpec
from medagent.llm import LLMResponse, ScriptedLLM, ToolCall
from medagent.observability.replay import read_trace
from medagent.planning.complexity import build_complexity_profile, tool_capabilities
from medagent.retrieval.backend import FakeRetrievalBackend
from medagent.retrieval.evidence import EvidenceBundle, EvidenceItem
from medagent.runtime.config import RuntimeConfig
from medagent.runtime.evaluation_inputs import FrozenRequest
from medagent.runtime.native_engine import NativeMedAgentEngine
from medagent.tools.registry import VISIBLE_TOOLS, ToolExecutionContext
from medagent.tools.schemas import RETRIEVAL_TOOLS, TOOL_SCHEMAS
from tests.test_semantic_validation import verdict

DATA = Path(__file__).resolve().parents[1] / "experiments/w7/frozen_v1"
CASES = json.loads((DATA / "cases.json").read_text(encoding="utf-8"))["cases"]
CORPUS = json.loads((DATA / "retrieval_corpus.json").read_text(encoding="utf-8"))
CODE, PAPER, GUIDE = "SYNTHETIC-C-COUGH", "SYNTHETIC-R-COUGH", "SYNTHETIC-G-COUGH-SPECIAL"
CODE_ANSWER = f"SYNTHETIC coding source only, not a real ICD code [EVIDENCE:{CODE}]"
PAPER_ANSWER = f"SYNTHETIC research paper, not a real publication [EVIDENCE:{PAPER}]"


def capture(case_id="E03", *, declarations=True):
    case = deepcopy(next(c for c in CASES if c["case_id"] == case_id))
    request = FrozenRequest.capture(
        case["description"],
        case["question"],
        RequestSpec.from_dict(case["expected_request_spec"]),
        AnswerContract.from_dict(case["expected_answer_contract"]),
        case["restrictions"],
        **(
            {"external_evidence_requirements": case["external_evidence_requirements"]}
            if declarations
            else {}
        ),
    )
    return case, request


def source(identifier):
    row = next(s for s in CORPUS["source_records"] if s["evidence_id"] == identifier)
    return EvidenceItem(**{f.name: deepcopy(row[f.name]) for f in fields(EvidenceItem)})


class CollectionFake(FakeRetrievalBackend):
    def __init__(self, mode="valid"):
        super().__init__([source(CODE), source(PAPER), source(GUIDE)])
        self.mode = mode

    async def search(self, query, collection, top_k):
        await super().search(query, collection, top_k)
        if "指南" in query:
            return [self.items[2]]
        if collection == "clinical_guidelines":
            return [] if self.mode == "no_code" else [self.items[0]]
        return [self.items[0] if self.mode == "code_only" else self.items[1]]


def e03_plan(role="research_agent", split=False):
    if split:
        return {
            "subtasks": [
                {
                    "subtask_id": f"t{i}",
                    "assigned_agent": role,
                    "description": f"RQ{i}",
                    "request_item_ids": [f"RQ{i}"],
                    "deliverable_ids": ["COMPREHENSIVE_CASE_ANALYSIS"],
                    "depends_on": ["t1"] if i == 2 else [],
                }
                for i in (1, 2)
            ]
        }
    return {
        "subtasks": [
            {
                "subtask_id": "both",
                "assigned_agent": role,
                "description": "Answer both independent source requests",
                "request_item_ids": ["RQ1", "RQ2"],
                "deliverable_ids": ["COMPREHENSIVE_CASE_ANALYSIS"],
            }
        ]
    }


def calls(*, code=True, swapped_target=False):
    tool_calls = [
        ToolCall(
            "paper",
            "search_knowledge",
            {"query": "SYNTHETIC cough papers", "request_item_id": "RQ2"},
        )
    ]
    if code:
        tool_calls.insert(
            0,
            ToolCall(
                "code",
                "disease_code",
                {
                    "disease_name": "SYNTHETIC cough",
                    "request_item_id": "RQ2" if swapped_target else "RQ1",
                },
            ),
        )
    return LLMResponse(tool_calls=tool_calls)


def answer_object(answers):
    return {
        "answers": [{"request_item_id": f"RQ{i}", "answer": a} for i, a in enumerate(answers, 1)]
    }


async def run_e03(path, *, mode="valid", replay=None, request=None, role="research_agent"):
    request = request or capture()[1]
    answers = [CODE_ANSWER, PAPER_ANSWER]
    if mode in {"no_lookup", "no_code"}:
        answers[0] = "No coding source was obtained; do not invent a code."
    if mode == "code_only":
        answers[1] = f"SYNTHETIC source [EVIDENCE:{CODE}]"
    if mode == "swapped":
        answers = [PAPER_ANSWER, CODE_ANSWER]
    llm = ScriptedLLM(
        ([e03_plan(role)] if replay is None else [])
        + [
            calls(code=mode != "no_lookup", swapped_target=mode == "wrong_target"),
            answer_object(answers),
        ]
    )
    backend = CollectionFake(mode)
    validator = ScriptedLLM([verdict(answers)])
    engine = NativeMedAgentEngine(
        RuntimeConfig(trace_dir=str(path), max_concurrent_workers=1 if replay else 4),
        llm=llm,
        validation_llm=validator,
        retrieval_backend=backend,
    )
    try:
        run = await engine.analyze_frozen(request, plan=replay)
        return run, backend, llm, validator, read_trace(path / run.result["run_id"])
    finally:
        await engine.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,expected_calls",
    [
        ("valid", 2),
        ("no_lookup", 1),
        ("no_code", 2),
        ("code_only", 2),
        ("swapped", 2),
        ("wrong_target", 1),
    ],
)
async def test_e03_real_worker_gates_and_rq_tool_filter(tmp_path, mode, expected_calls):
    run, backend, llm, validator, events = await run_e03(tmp_path, mode=mode)
    assert len(backend.requests) == expected_calls
    assert len(llm.requests) == 3
    checks = {i["request_item_id"]: i for i in run.result["evidence_validation"]["items"]}
    assert checks["RQ1"]["required"] is True and checks["RQ1"]["status"] != "not_required"
    if mode == "valid":
        assert run.result["status"] == "completed" and len(validator.requests) == 1
        assert [i["status"] for i in checks.values()] == ["satisfied", "satisfied"]
        assert checks["RQ1"]["source_event_ids"] != checks["RQ2"]["source_event_ids"]
        assert events[-1]["payload"]["status"] == "completed"
    else:
        assert run.result["status"] == "incomplete" and validator.requests == []
        assert any(i["status"] not in {"satisfied", "not_required"} for i in checks.values())
        assert events[-1]["payload"]["status"] == "incomplete"
    if mode == "code_only":
        worker_check = next(
            e["payload"]
            for e in events
            if e["event_type"] == "evidence_acceptance" and e["stage"] == "worker"
        )
        assert worker_check["items"][0]["status"] == "satisfied"
        assert checks["RQ2"]["status"] == "missing_evidence_kind"
    if mode == "swapped":
        assert checks["RQ1"]["status"] == checks["RQ2"]["status"] == "invalid_citation"
    # ScriptedLLM retains list references; later tool continuation appends to them.
    initial = json.dumps([r["messages"][:2] for r in llm.requests[:2]])
    assert "external_evidence_requirements" in initial
    assert "success_source_ids" not in initial and "completion_rubric" not in initial
    assert CODE not in initial and PAPER not in initial


@pytest.mark.asyncio
async def test_e03_role_whitelist_still_denies_code_and_cannot_complete(tmp_path):
    run, backend, llm, _, _ = await run_e03(tmp_path, role="consultation_agent")
    assert len(backend.requests) == 1
    assert run.result["status"] == "incomplete"
    assert "disease_code" not in {s["function"]["name"] for s in llm.requests[1]["tools"]}
    assert "disease_code" not in VISIBLE_TOOLS["consultation_agent"]
    case, request = capture()
    spec, contract, _ = request.materialize(case["description"], case["question"])
    bundle = EvidenceBundle("", "", [], evidence_requirements=request.evidence_requirements)
    backend = CollectionFake()
    engine = NativeMedAgentEngine(RuntimeConfig(), llm=ScriptedLLM([]), retrieval_backend=backend)
    try:
        profile = build_complexity_profile(
            case["question"],
            contract,
            spec,
            frozen_evidence_requirements=request.evidence_requirements,
        )
        tools = engine._tool_registry(case["question"], bundle, tool_capabilities(profile))
        with pytest.raises(PermissionError):
            await tools.execute(
                "consultation_agent",
                "disease_code",
                {"disease_name": "cough"},
                context=ToolExecutionContext("t", "RQ1", (), (spec.items[0],)),
            )
        assert backend.requests == []
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_replay_request_and_conditions_bind_declared_evidence(tmp_path):
    _, request = capture()
    original, _, _, _, _ = await run_e03(tmp_path / "c", request=request)
    replayed, backend, llm, _, events = await run_e03(
        tmp_path / "serial",
        request=request,
        replay=original.plan,
    )
    assert original.plan == replayed.plan and original.plan.request_sha256 == request.sha256
    assert replayed.result["status"] == "completed" and len(backend.requests) == 2
    assert len(llm.requests) == 2
    assert (
        next(e for e in events if e["event_type"] == "plan_created")["payload"][
            "planner_call_count"
        ]
        == 0
    )
    value = json.loads(request.external_evidence_json)
    value["requirements"][0].update(
        source_category="external", acceptable_source_categories=["classification", "research"]
    )
    changed = replace(request, external_evidence_json=json.dumps(value))
    assert changed.sha256 != request.sha256
    llm, backend = ScriptedLLM([]), CollectionFake()
    engine = NativeMedAgentEngine(
        RuntimeConfig(trace_dir=str(tmp_path / "reject"), max_concurrent_workers=1),
        llm=llm,
        validation_llm=ScriptedLLM([]),
        retrieval_backend=backend,
    )
    try:
        with pytest.raises(ValueError, match="conditions mismatch"):
            await engine.analyze_frozen(changed, plan=original.plan)
        assert backend.requests == llm.requests == []
        assert (
            read_trace(next((tmp_path / "reject").iterdir()))[-1]["payload"]["status"] == "failed"
        )
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_explicit_spec_does_not_fall_back_to_lexical_evidence(tmp_path, monkeypatch):
    def forbidden(*args):
        raise AssertionError("frozen evidence must not be inferred from text")

    for module in ("planning.complexity", "runtime.coverage", "runtime.handoff"):
        monkeypatch.setattr(f"medagent.{module}.evidence_requirements", forbidden)
    run, backend, _, _, _ = await run_e03(tmp_path)
    assert run.result["status"] == "completed" and len(backend.requests) == 2


@pytest.mark.parametrize(
    "change",
    [
        "rq",
        "priority",
        "category",
        "accepted",
        "duplicate",
        "self",
        "unknown_upstream",
        "reuse_empty",
        "fresh_upstream",
        "success_ids",
        "answer",
        "admission",
    ],
)
def test_invalid_declarations_are_rejected_before_execution(change):
    case, _ = capture()
    rows = case["external_evidence_requirements"]
    if change == "rq":
        rows[0]["request_item_id"] = "RQ99"
    elif change == "priority":
        rows[0]["required"] = False
    elif change == "category":
        rows[0]["source_category"] = "paper"
    elif change == "accepted":
        rows[0]["acceptable_source_categories"] = ["research"]
    elif change == "duplicate":
        rows.append(deepcopy(rows[0]))
    elif change == "self":
        rows[0].update(execution="reuse_only", upstream_request_item_ids=["RQ1"])
    elif change == "unknown_upstream":
        rows[0].update(execution="reuse_only", upstream_request_item_ids=["RQ9"])
    elif change == "reuse_empty":
        rows[0]["execution"] = "reuse_only"
    elif change == "fresh_upstream":
        rows[0]["upstream_request_item_ids"] = ["RQ2"]
    elif change == "success_ids":
        rows[0]["success_source_ids"] = [CODE]
    elif change == "answer":
        rows[0]["expected_answer"] = CODE_ANSWER
    else:
        rows[0]["requires_admission_delivery_citation"] = False
    with pytest.raises(ValueError):
        FrozenRequest.capture(
            case["description"],
            case["question"],
            RequestSpec.from_dict(case["expected_request_spec"]),
            AnswerContract.from_dict(case["expected_answer_contract"]),
            external_evidence_requirements=rows,
        )


def test_frozen_evidence_is_detached_canonical_and_sha_bound():
    case, request = capture()
    rows = deepcopy(case["external_evidence_requirements"])
    rows.reverse()
    same = FrozenRequest.capture(
        case["description"],
        case["question"],
        RequestSpec.from_dict(case["expected_request_spec"]),
        AnswerContract.from_dict(case["expected_answer_contract"]),
        external_evidence_requirements=rows,
    )
    assert same == request and same.sha256 == request.sha256
    rows[0]["acceptable_source_categories"].clear()
    assert same.evidence_requirements == request.evidence_requirements
    assert (
        replace(
            request,
            external_evidence_json=json.dumps(json.loads(request.external_evidence_json), indent=4),
        )
        == request
    )
    with pytest.raises(ValueError):
        replace(request, external_evidence_json={})
    assert capture(declarations=False)[1].sha256 != request.sha256


@pytest.mark.asyncio
@pytest.mark.parametrize("attempt_fresh", [False, True])
async def test_reuse_only_uses_declared_handoff_without_a_new_lookup(tmp_path, attempt_fresh):
    _, request = capture("D05")
    plan = {
        "subtasks": [
            {
                "subtask_id": "lookup",
                "description": "指南检索",
                "assigned_agent": "research_agent",
                "request_item_ids": ["RQ1"],
                "deliverable_ids": ["COMPREHENSIVE_CASE_ANALYSIS"],
            },
            {
                "subtask_id": "reuse",
                "description": "显式复用总结",
                "assigned_agent": "consultation_agent",
                "request_item_ids": ["RQ2"],
                "deliverable_ids": ["TREATMENT_PLAN"],
                "depends_on": ["lookup"],
            },
        ]
    }
    answers = [
        f"SYNTHETIC guideline only [EVIDENCE:{GUIDE}]",
        f"Treatment management: conditional principles from the same source [EVIDENCE:{GUIDE}]",
    ]
    scripted = [
        plan,
        LLMResponse(
            tool_calls=[
                ToolCall(
                    "guide",
                    "clinical_guideline",
                    {
                        "query": "SYNTHETIC 咳嗽指南",
                        "request_item_id": "RQ1",
                    },
                )
            ]
        ),
        {"answers": [{"request_item_id": "RQ1", "answer": answers[0]}]},
    ]
    if attempt_fresh:
        scripted.append(
            LLMResponse(
                tool_calls=[
                    ToolCall(
                        "forbidden-fresh",
                        "clinical_guideline",
                        {
                            "query": "SYNTHETIC 咳嗽指南",
                            "request_item_id": "RQ2",
                        },
                    )
                ]
            )
        )
    scripted.extend(
        [{"answers": [{"request_item_id": "RQ2", "answer": answers[1]}]}, answer_object(answers)]
    )
    llm = ScriptedLLM(scripted)
    backend = CollectionFake()
    engine = NativeMedAgentEngine(
        RuntimeConfig(trace_dir=str(tmp_path)),
        llm=llm,
        validation_llm=ScriptedLLM(
            [
                verdict(
                    answers, restrictions=["satisfied"], issues=["false_positive", "false_positive"]
                )
            ]
        ),
        retrieval_backend=backend,
    )
    try:
        run = await engine.analyze_frozen(request)
        assert run.result["status"] == "completed" and len(backend.requests) == 1
        target = json.loads(llm.requests[3]["messages"][1]["content"])
        assert target["upstream_handoffs"][0]["request_item_answers"][0]["request_item_id"] == "RQ1"
        assert not ({s["function"]["name"] for s in llm.requests[3]["tools"]} & RETRIEVAL_TOOLS)
        check = run.result["evidence_validation"]["items"][1]
        assert check["status"] == "satisfied" and check["handoff_event_ids"]
        assert check["origin_request_item_ids"] == ["RQ1"]
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_optional_declared_evidence_does_not_become_must(tmp_path):
    _, request = capture("E06")
    plan = e03_plan("research_agent")
    answer = "已知信息：成人咳嗽一天，未提供其他资料。"
    llm = ScriptedLLM([plan, {"answers": [{"request_item_id": "RQ1", "answer": answer}]}])
    backend = CollectionFake()
    engine = NativeMedAgentEngine(
        RuntimeConfig(trace_dir=str(tmp_path)),
        llm=llm,
        validation_llm=ScriptedLLM([verdict([answer, ""], ["satisfied", "unanswered"])]),
        retrieval_backend=backend,
    )
    try:
        run = await engine.analyze_frozen(request)
        assert run.result["status"] == "completed" and backend.requests == []
        assert run.result["evidence_validation"]["items"][1]["required"] is False
        assert run.result["evidence_validation"]["items"][1]["status"] == "not_required"
    finally:
        await engine.close()


def test_original_role_limits_and_tool_schemas_are_unchanged():
    assert "disease_code" not in VISIBLE_TOOLS["consultation_agent"]
    assert TOOL_SCHEMAS["disease_code"]["function"]["parameters"]["required"] == ["disease_name"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["valid", "code_only"])
async def test_e03_independent_workers_keep_per_rq_evidence_status(tmp_path, mode):
    _, request = capture()
    plan = {
        "subtasks": [
            {
                "subtask_id": "code",
                "description": "Retrieve the coding source",
                "assigned_agent": "research_agent",
                "request_item_ids": ["RQ1"],
                "deliverable_ids": ["COMPREHENSIVE_CASE_ANALYSIS"],
            },
            {
                "subtask_id": "paper",
                "description": "Retrieve the paper",
                "assigned_agent": "diagnostic_agent",
                "request_item_ids": ["RQ2"],
                "deliverable_ids": ["COMPREHENSIVE_CASE_ANALYSIS"],
            },
        ]
    }
    paper_answer = PAPER_ANSWER if mode == "valid" else CODE_ANSWER
    llm = ScriptedLLM(
        [
            plan,
            LLMResponse(
                tool_calls=[
                    ToolCall(
                        "code",
                        "disease_code",
                        {"disease_name": "SYNTHETIC cough", "request_item_id": "RQ1"},
                    )
                ]
            ),
            {"answers": [{"request_item_id": "RQ1", "answer": CODE_ANSWER}]},
            LLMResponse(
                tool_calls=[
                    ToolCall(
                        "paper",
                        "search_knowledge",
                        {"query": "SYNTHETIC cough research", "request_item_id": "RQ2"},
                    )
                ]
            ),
            {"answers": [{"request_item_id": "RQ2", "answer": paper_answer}]},
            answer_object([CODE_ANSWER, paper_answer]),
        ]
    )
    backend = CollectionFake(mode)
    engine = NativeMedAgentEngine(
        RuntimeConfig(trace_dir=str(tmp_path)),
        llm=llm,
        validation_llm=ScriptedLLM([verdict([CODE_ANSWER, paper_answer])]),
        retrieval_backend=backend,
    )
    try:
        run = await engine.analyze_frozen(request)
        assert len(backend.requests) == 2
        assert len(run.plan.materialize().subtasks) == 2
        checks = run.result["evidence_validation"]["items"]
        assert checks[0]["status"] == "satisfied"
        assert checks[1]["status"] == ("satisfied" if mode == "valid" else "missing_evidence_kind")
        assert run.result["status"] == ("completed" if mode == "valid" else "incomplete")
        assert len(llm.requests) == (6 if mode == "valid" else 5)
        task_code = {s["function"]["name"] for s in llm.requests[1]["tools"]}
        task_paper = {s["function"]["name"] for s in llm.requests[3]["tools"]}
        assert "disease_code" in task_code and "search_knowledge" not in task_code
        assert "search_knowledge" in task_paper and "disease_code" not in task_paper
    finally:
        await engine.close()


@pytest.mark.parametrize("change", ["cycle", "missing_source", "optional_source"])
def test_invalid_declared_reuse_relationships_are_rejected(change):
    case, _ = capture("D05" if change != "optional_source" else "E06")
    rows = case["external_evidence_requirements"]
    if change == "cycle":
        rows[0].update(execution="reuse_only", upstream_request_item_ids=["RQ2"])
    elif change == "missing_source":
        rows.pop(0)
    else:
        source_row = deepcopy(rows[0])
        source_row.update(
            request_item_id="RQ1",
            required=True,
            execution="reuse_only",
            upstream_request_item_ids=["RQ2"],
        )
        rows.append(source_row)
    with pytest.raises(ValueError):
        FrozenRequest.capture(
            case["description"],
            case["question"],
            RequestSpec.from_dict(case["expected_request_spec"]),
            AnswerContract.from_dict(case["expected_answer_contract"]),
            case["restrictions"],
            external_evidence_requirements=rows,
        )


@pytest.mark.asyncio
async def test_reuse_plan_without_dependency_rejects_before_worker_calls(tmp_path):
    _, request = capture("D05")
    plan = {
        "subtasks": [
            {
                "subtask_id": "single",
                "description": request.question,
                "assigned_agent": "consultation_agent",
                "request_item_ids": ["RQ1", "RQ2"],
                "deliverable_ids": ["COMPREHENSIVE_CASE_ANALYSIS", "TREATMENT_PLAN"],
            }
        ]
    }
    llm, backend = ScriptedLLM([plan]), CollectionFake()
    engine = NativeMedAgentEngine(
        RuntimeConfig(trace_dir=str(tmp_path)),
        llm=llm,
        validation_llm=ScriptedLLM([]),
        retrieval_backend=backend,
    )
    try:
        with pytest.raises(ValueError, match="upstream task dependency"):
            await engine.analyze_frozen(request)
        assert len(llm.requests) == 1 and backend.requests == []
        assert read_trace(next(tmp_path.iterdir()))[-1]["payload"]["status"] == "failed"
    finally:
        await engine.close()
