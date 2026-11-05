"""Real offline runtime paths for RQ-scoped provenance, intent and private causality."""
from __future__ import annotations

import json
from uuid import UUID, uuid4

import pytest

from medagent.context.request_spec import RequestItem, RequestSpec
from medagent.llm import LLMResponse, ScriptedLLM, ToolCall
from medagent.observability.privacy import metadata_payload
from medagent.observability.replay import read_trace
from medagent.planning.complexity import evidence_requirements
from medagent.retrieval.backend import OffRetrievalBackend
from medagent.retrieval.evidence import EvidenceBundle, EvidenceItem
from medagent.runtime.config import RuntimeConfig
from medagent.runtime.coverage import evaluate_evidence_coverage
from medagent.runtime.native_engine import NativeMedAgentEngine


class ScopedBackend:
    def __init__(self):
        self.requests = []

    async def search(self, query, collection, top_k):
        self.requests.append((query, collection))
        identifier = "beta" if "beta" in query else "alpha"
        return [EvidenceItem(identifier, "doc-" + identifier, "source-" + identifier,
                             "Synthetic source", None, "Offline fixture", None,
                             "PRIVATE_SOURCE_BODY " + identifier, 0.9, 1,
                             metadata={"source_kind": "classification" if "ICD" in query else
                                       "guideline" if collection == "clinical_guidelines"
                                       else "research"})]


def draft(answers):
    return {"answers": [{"request_item_id": key, "answer": text}
                        for key, text in answers.items()]}


class SourceInventoryLLM(ScriptedLLM):
    """Independent source partition and schema-valid W2 replies, not an evidence ledger."""
    async def complete(self, messages, **kwargs):
        self.requests.append({"messages": messages, **kwargs})
        payload = json.loads(messages[-1]["content"])
        if "final_answers" not in payload:
            value = {"segments": [{"start": 0, "end": len(payload["question"]),
                                    "kind": "required"}]}
        else:
            answers = payload["final_answers"]
            value = {
                "items": [{"request_item_id": key, "status": "satisfied", "evidence": text,
                           "reason": "Synthetic answer covers the fixture request"}
                          for key, text in answers.items()],
                "restrictions": [],
                "guardrail": [{"index": i, "status": "false_positive",
                               "evidence": next(iter(answers.values())),
                               "reason": "Synthetic fixture uses different wording"}
                              for i in range(len(payload["guardrail_issues"]))],
            }
        return LLMResponse(json.dumps(value), finish_reason="stop")


async def execute(tmp_path, question, tasks, responses, *, backend=None, semantic=False):
    llm = ScriptedLLM([{"subtasks": tasks}, *responses])
    verifier = SourceInventoryLLM([])
    backend = backend if backend is not None else ScopedBackend()
    engine = NativeMedAgentEngine(
        RuntimeConfig(trace_dir=str(tmp_path), semantic_validation_enabled=semantic),
        llm=llm, validation_llm=verifier, retrieval_backend=backend,
    )
    try:
        result = await engine.analyze("PRIVATE_CASE synthetic reference range 2 to 8.", question)
    finally:
        await engine.close()
    return result, read_trace(tmp_path / result["run_id"]), backend, llm, verifier


SINGLE = [{"subtask_id": "one", "assigned_agent": "research_agent",
           "description": "Answer only the requested item", "request_item_ids": ["RQ1"]}]


@pytest.mark.parametrize(("question", "expected"), [
    ("Consult external materials and identify the sources.", {"external"}),
    ("Explain the reference range supplied in the case.", set()),
    ("What does the word guideline mean?", set()),
    ("Find current clinical guidelines and cite the sources.", {"guideline"}),
    ("查询已提供病例内容中的参考范围。", set()),
    ("请查阅外部材料并注明来源。", {"external"}),
    ("说明病例中已提供资料的含义。", set()),
    ("请检索临床指南并引用来源。", {"guideline"}),
    ("Find published papers.", {"research"}),
    ("Retrieve an ICD disease code.", {"classification"}),
])
def test_source_action_is_distinct_from_mention(question, expected):
    assert evidence_requirements(question) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("question", [
    "Explain the reference range supplied in the case.",
    "What does the word guideline mean?",
    "查询已提供病例内容中的参考范围。",
])
async def test_ordinary_explanation_completes_with_w1_w2_enabled(tmp_path, question):
    result, _, backend, _, verifier = await execute(
        tmp_path, question, SINGLE, [draft({"RQ1": "The supplied interval is 2 to 8."})],
        semantic=True,
    )
    assert result["status"] == "completed" and not backend.requests
    assert len(verifier.requests) == 3
    assert result["evidence_validation"]["items"][0]["status"] == "not_required"


@pytest.mark.asyncio
@pytest.mark.parametrize("retrieved", [True, False])
@pytest.mark.parametrize("question", [
    "Consult external materials and identify the sources.",
    "Find current clinical guidelines and cite the sources.",
    "Find published papers.",
    "请查阅外部材料并注明来源。",
])
async def test_w1_w2_full_path_cannot_replace_actual_retrieval(tmp_path, retrieved, question):
    tool = "clinical_guideline" if "guideline" in question else "search_knowledge"
    calls = [LLMResponse(tool_calls=[ToolCall("c1", tool, {"query": "alpha"})])] \
        if retrieved else []
    answer = "Retrieved source [EVIDENCE:alpha]" if retrieved else "I consulted external sources."
    result, events, backend, llm, verifier = await execute(
        tmp_path, question, SINGLE, [*calls, draft({"RQ1": answer})], semantic=True,
    )
    assert result["status"] == ("completed" if retrieved else "incomplete")
    assert len(backend.requests) == int(retrieved)
    assert len(verifier.requests) == (3 if retrieved else 2)
    assert len(llm.requests) == (3 if retrieved else 2)
    assert any(e["event_type"] == "evidence_acceptance" for e in events)


MULTI_Q = "1. Retrieve guidelines for alpha.\n2. Retrieve guidelines for beta."
MULTI_TASK = [{"subtask_id": "both", "assigned_agent": "research_agent",
               "description": "Handle alpha and beta", "request_item_ids": ["RQ1", "RQ2"]}]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["ambiguous", "foreign", "reuse", "correct"])
async def test_same_worker_cannot_pool_rq_authorization(tmp_path, mode):
    arguments = {"query": "alpha"}
    if mode != "ambiguous":
        arguments["request_item_id"] = "RQ99" if mode == "foreign" else "RQ1"
    calls = [ToolCall("c1", "clinical_guideline", arguments)]
    if mode == "correct":
        calls.append(ToolCall("c2", "clinical_guideline", {
            "query": "beta", "request_item_id": "RQ2",
        }))
    result, events, backend, _, _ = await execute(tmp_path, MULTI_Q, MULTI_TASK, [
        LLMResponse(tool_calls=calls), draft({
            "RQ1": "Alpha source [EVIDENCE:alpha]",
            "RQ2": "Beta source [EVIDENCE:beta]" if mode == "correct"
            else "Claiming alpha for beta [EVIDENCE:alpha]",
        }),
    ])
    assert result["status"] == ("completed" if mode == "correct" else "incomplete")
    assert len(backend.requests) == {"ambiguous": 0, "foreign": 0, "reuse": 1, "correct": 2}[mode]
    if backend.requests:
        assert "beta" not in backend.requests[0][0]
    if mode == "correct":
        assert "alpha" not in backend.requests[1][0]
        queries = [e for e in events if e["event_type"] == "retrieval_query"]
        assert [e["payload"]["request_item_ids"] for e in queries] == [["RQ1"], ["RQ2"]]
    if mode == "reuse":
        assert result["evidence_validation"]["items"][1]["status"] == "invalid_citation"


@pytest.mark.asyncio
@pytest.mark.parametrize(("upstream", "downstream", "tool", "argument", "complete"), [
    ("Retrieve an ICD disease code.", "Find published papers using the upstream result.",
     "disease_code", "disease_name", False),
    ("Retrieve clinical guidelines.", "Find published papers using those guidelines.",
     "clinical_guideline", "query", False),
    ("Retrieve clinical guidelines.", "Retrieve new guidelines based on the upstream result.",
     "clinical_guideline", "query", False),
    ("Retrieve clinical guidelines.", "Explain a treatment plan using those guidelines.",
     "clinical_guideline", "query", True),
])
async def test_handoff_needs_explicit_reuse_and_matching_kind(
    tmp_path, upstream, downstream, tool, argument, complete,
):
    question = f"1. {upstream}\n2. {downstream}"
    tasks = [
        {"subtask_id": "up", "assigned_agent": "research_agent", "description": upstream,
         "request_item_ids": ["RQ1"], "depends_on": []},
        {"subtask_id": "down", "assigned_agent": "consultation_agent", "description": downstream,
         "request_item_ids": ["RQ2"], "depends_on": ["up"]},
    ]
    answers = {"RQ1": "Upstream source [EVIDENCE:alpha]",
               "RQ2": "Downstream answer [EVIDENCE:alpha]"}
    result, events, backend, llm, _ = await execute(tmp_path, question, tasks, [
        LLMResponse(tool_calls=[ToolCall("c1", tool, {argument: "alpha"})]),
        draft({"RQ1": answers["RQ1"]}), draft({"RQ2": answers["RQ2"]}), draft(answers),
    ])
    assert result["status"] == ("completed" if complete else "incomplete")
    assert len(backend.requests) == 1
    assert len(llm.requests) == (5 if complete else 4)
    if not complete:
        assert not any(e["event_type"] == "synthesis_input" for e in events)
        assert result["evidence_validation"]["items"][1]["status"] == "invalid_citation"
    else:
        by_id = {e["event_id"]: e for e in events}
        query = next(e for e in events if e["event_type"] == "retrieval_query")
        source = next(e for e in events if e["event_type"] == "retrieval_result")
        handoff = next(e for e in events if e["event_type"] == "worker_handoff")
        assert query["payload"]["request_item_ids"] == ["RQ1"]
        assert source["parent_event_id"] == query["event_id"]
        assert source["payload"]["admitted_count"] == 1
        assert handoff["payload"]["request_item_ids"] == ["RQ2"]
        assert handoff["payload"]["origin_request_item_ids"] == ["RQ1"]
        assert handoff["payload"]["source_event_ids"] == [source["event_id"]]
        final = next(e for e in reversed(events) if e["event_type"] == "evidence_acceptance")
        item = final["payload"]["items"][1]
        assert item["request_item_id"] == "RQ2"
        assert item["evidence_links"] == [{
            "request_item_id": "RQ2", "origin_request_item_id": "RQ1",
            "source_event_id": source["event_id"], "handoff_event_id": handoff["event_id"],
        }]
        assert by_id[item["source_event_ids"][0]]["event_type"] == "retrieval_result"
        assert str(UUID(query["payload"]["retrieval_id"])) == query["payload"]["retrieval_id"]
        disk = json.dumps(events)
        for private in ["PRIVATE_CASE", "PRIVATE_SOURCE_BODY", "alpha", "clinical_guidelines"]:
            assert private not in disk


@pytest.mark.asyncio
async def test_optional_missing_source_does_not_block_required_answer(tmp_path):
    result, _, backend, _, _ = await execute(
        tmp_path, "1. Explain the supplied range.\n2. Optional: retrieve external materials.",
        [{"subtask_id": "both", "assigned_agent": "diagnostic_agent", "description": "Explain",
          "request_item_ids": ["RQ1", "RQ2"]}],
        [draft({"RQ1": "The supplied reference range is 2 to 8."})],
    )
    assert result["status"] == "completed" and not backend.requests


@pytest.mark.parametrize("field", ["source_event_id", "handoff_event_id"])
def test_metadata_rejects_free_text_in_evidence_links(field):
    projected = metadata_payload({"evidence_links": [{
        "request_item_id": "RQ1", "origin_request_item_id": "PRIVATE_CASE",
        field: "PRIVATE_CASE", "evidence_id": "PRIVATE_CASE", "query": "PRIVATE_CASE",
    }]})
    assert projected == {"evidence_links": [{"request_item_id": "RQ1"}]}


@pytest.mark.asyncio
async def test_disabled_backend_still_blocks_recognized_external_materials(tmp_path):
    result, _, _, _, _ = await execute(tmp_path,
        "Consult external materials and identify the sources.", SINGLE, [
            LLMResponse(tool_calls=[ToolCall("c1", "search_knowledge", {"query": "alpha"})]),
            draft({"RQ1": "No source available."}),
        ], backend=OffRetrievalBackend(), semantic=True)
    assert result["status"] == "incomplete"
    assert result["evidence_validation"]["items"][0]["status"] == "disabled"


@pytest.mark.asyncio
async def test_fresh_rq_lookup_cannot_substitute_guideline_for_papers(tmp_path):
    result, _, backend, _, _ = await execute(
        tmp_path, "1. Retrieve guidelines for alpha.\n2. Find published papers for beta.",
        MULTI_TASK, [LLMResponse(tool_calls=[
            ToolCall("c1", "clinical_guideline", {"query": "alpha", "request_item_id": "RQ1"}),
            ToolCall("c2", "clinical_guideline", {"query": "beta", "request_item_id": "RQ2"}),
        ]), draft({"RQ1": "Alpha [EVIDENCE:alpha]", "RQ2": "Paper claim [EVIDENCE:beta]"})],
    )
    assert len(backend.requests) == 2 and result["status"] == "incomplete"
    assert result["evidence_validation"]["items"][1]["status"] == "missing_evidence_kind"


@pytest.mark.asyncio
async def test_target_argument_cannot_reset_tool_budget(tmp_path):
    result, events, backend, _, _ = await execute(tmp_path, MULTI_Q, MULTI_TASK, [
        LLMResponse(tool_calls=[ToolCall("bad", "clinical_guideline", {"query": "alpha"})]),
        LLMResponse(tool_calls=[ToolCall("c1", "clinical_guideline", {
            "query": "alpha", "request_item_id": "RQ1",
        })]),
        LLMResponse(tool_calls=[ToolCall("c2", "clinical_guideline", {
            "query": "beta", "request_item_id": "RQ2",
        })]),
    ])
    assert result["status"] == "incomplete" and len(backend.requests) == 1
    assert result["execution_failures"][0]["worker_status"] == "budget_exhausted"
    assert len([e for e in events if e["event_type"] == "tool_call"]) == 2


@pytest.mark.asyncio
async def test_changing_target_does_not_bypass_duplicate_call_id(tmp_path):
    result, _, backend, _, _ = await execute(tmp_path, MULTI_Q, MULTI_TASK, [
        LLMResponse(tool_calls=[
            ToolCall("same", "clinical_guideline", {"query": "alpha", "request_item_id": "RQ1"}),
            ToolCall("same", "clinical_guideline", {"query": "beta", "request_item_id": "RQ2"}),
        ]),
    ])
    assert result["status"] == "incomplete" and len(backend.requests) == 0
    assert result["execution_failures"][0]["worker_status"] == "protocol_error"
    assert result["execution_failures"][0]["failure_reason"] == "duplicate_tool_call_id"


@pytest.mark.asyncio
async def test_metadata_fanin_retains_each_origin_and_handoff_link(tmp_path):
    question = ("1. Retrieve guidelines for alpha.\n2. Find published papers for beta.\n"
                "3. Explain a treatment plan using those guidelines and papers.")
    tasks = [
        {"subtask_id": "guide", "assigned_agent": "research_agent", "description": "Guidelines",
         "request_item_ids": ["RQ1"], "depends_on": []},
        {"subtask_id": "paper", "assigned_agent": "research_agent", "description": "Papers",
         "request_item_ids": ["RQ2"], "depends_on": []},
        {"subtask_id": "combine", "assigned_agent": "consultation_agent", "description": "Combine",
         "request_item_ids": ["RQ3"], "depends_on": ["guide", "paper"]},
    ]
    answers = {"RQ1": "Guidelines [EVIDENCE:alpha]", "RQ2": "Papers [EVIDENCE:beta]",
               "RQ3": "Plan [EVIDENCE:alpha] [EVIDENCE:beta]"}
    result, events, backend, _, _ = await execute(tmp_path, question, tasks, [
        LLMResponse(tool_calls=[ToolCall("g", "clinical_guideline", {"query": "alpha"})]),
        draft({"RQ1": answers["RQ1"]}),
        LLMResponse(tool_calls=[ToolCall("p", "search_knowledge", {"query": "beta"})]),
        draft({"RQ2": answers["RQ2"]}), draft({"RQ3": answers["RQ3"]}), draft(answers),
    ])
    assert result["status"] == "completed" and len(backend.requests) == 2
    by_id = {e["event_id"]: e for e in events}
    links = result["evidence_validation"]["items"][2]["evidence_links"]
    assert len(links) == 2
    assert {link["origin_request_item_id"] for link in links} == {"RQ1", "RQ2"}
    for link in links:
        assert link["request_item_id"] == "RQ3"
        source = by_id[link["source_event_id"]]
        handoff = by_id[link["handoff_event_id"]]
        query = by_id[source["parent_event_id"]]
        assert source["event_type"] == "retrieval_result"
        assert query["payload"]["request_item_ids"] == [link["origin_request_item_id"]]
        assert handoff["payload"]["source_event_ids"] == [source["event_id"]]
        assert handoff["payload"]["request_item_ids"] == ["RQ3"]
    assert "PRIVATE_SOURCE_BODY" not in json.dumps(events)


@pytest.mark.asyncio
@pytest.mark.parametrize("category", [None, "guideline", "research"])
async def test_paper_request_needs_declared_document_kind_not_just_generic_tool(tmp_path, category):
    class Catalogue(ScopedBackend):
        async def search(self, *args):
            items = await super().search(*args)
            items[0].metadata = {"source_kind": category} if category else {}
            return items

    backend = Catalogue()
    result, _, _, _, _ = await execute(tmp_path, "Find published papers.", SINGLE, [
        LLMResponse(tool_calls=[ToolCall("c1", "search_knowledge", {"query": "alpha"})]),
        draft({"RQ1": "Paper claim [EVIDENCE:alpha]"}),
    ], backend=backend)
    assert len(backend.requests) == 1
    assert result["status"] == ("completed" if category == "research" else "incomplete")
    if category != "research":
        assert result["evidence_validation"]["items"][0]["status"] == "missing_evidence_kind"


def test_same_rq_different_workers_cannot_borrow_each_others_receipt():
    spec = RequestSpec([RequestItem("RQ1", "Find published papers.", True, 1,
                                    "Find published papers.")])
    source_id = str(uuid4())
    bundle = EvidenceBundle("", "", deliveries=[
        {"subtask_id": "other", "request_item_id": "RQ1", "origin_request_item_id": "RQ1",
         "evidence_id": "shared", "tool": "search_knowledge", "source_kind": "research",
         "source_event_id": source_id},
        {"subtask_id": "current", "request_item_id": "RQ1", "origin_request_item_id": "RQ1",
         "evidence_id": "shared", "tool": "clinical_guideline", "source_kind": "guideline",
         "source_event_id": str(uuid4())},
    ])
    answers, allowed = {"RQ1": "Claim [EVIDENCE:shared]"}, {"RQ1": {"shared"}}
    current = evaluate_evidence_coverage(spec, answers, bundle, allowed, subtask_id="current")
    assert current["items"][0]["status"] == "missing_evidence_kind"
    bundle.accepted_by_task[("other", "RQ1")] = {"shared"}
    final = evaluate_evidence_coverage(spec, answers, bundle, allowed)
    assert final["complete"]
    assert final["items"][0]["source_event_ids"] == [source_id]
