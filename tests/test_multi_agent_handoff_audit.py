"""C1 offline regression tests for dependency scheduling and public handoffs."""

import asyncio
import json

import pytest

from medagent.agents.base import RequestItemAnswer, WorkerResult
from medagent.context.contract import build_answer_contract
from medagent.context.request_spec import build_request_spec
from medagent.guardrails.sanitizer import sanitize_answer
from medagent.llm import LLMResponse, ScriptedLLM
from medagent.observability.replay import read_trace, replay
from medagent.planning.complexity import build_complexity_profile
from medagent.planning.models import Plan, PlanDependencyError, Subtask
from medagent.planning.planner import Planner, apply_contract_policy
from medagent.runtime.config import RuntimeConfig
from medagent.runtime.handoff import build_handoff
from medagent.runtime.native_engine import NativeMedAgentEngine


def _with_private_block(public, reasoning_format):
    tag = "think" if reasoning_format.startswith("think") else "analysis"
    opening = f"<{tag}>DO_NOT_HANDOFF_PRIVATE_REASONING"
    return public + opening if reasoning_format.endswith("open") else opening + f"</{tag}>" + public


@pytest.mark.parametrize("reasoning_format", ["analysis", "think", "analysis_open", "think_open"])
@pytest.mark.parametrize(
    "scenario",
    [
        "independent",
        "dependent",
        "upstream_failure",
        "upstream_timeout",
        "upstream_empty",
        "cleaned_empty",
    ],
)
def test_parallel_or_dependency_execution_and_failure_gate(tmp_path, scenario, reasoning_format):
    independent = scenario == "independent"
    question = (
        "1. Give a diagnosis.\n2. Summarize independent research evidence."
        if independent
        else "1. Give a diagnosis.\n2. Give a treatment plan based on that diagnosis."
    )
    downstream = "research_agent" if independent else "consultation_agent"
    raw_plan = {
        "subtasks": [
            {
                "subtask_id": "diagnose",
                "assigned_agent": "diagnostic_agent",
                "description": "Determine the diagnosis independently.",
                "deliverable_ids": ["DIAGNOSIS_WITH_BASIS"],
                "request_item_ids": ["RQ1"],
                "depends_on": [],
            },
            {
                "subtask_id": "research" if independent else "treat",
                "assigned_agent": downstream,
                "description": "Summarize independent literature."
                if independent
                else "Wait for diagnose, then use its result to prepare treatment.",
                "deliverable_ids": ["DIAGNOSIS_WITH_BASIS" if independent else "TREATMENT_PLAN"],
                "request_item_ids": ["RQ2"],
                "depends_on": [] if independent else ["diagnose"],
            },
        ]
    }
    diagnostic_marker = "UPSTREAM_DIAGNOSIS_AUDIT_MARKER"
    downstream_marker = "SECOND_WORKER_AUDIT_MARKER"

    class BarrierLLM(ScriptedLLM):
        def __init__(self):
            super().__init__([raw_plan, {"answers": []}])
            self.worker_inputs = {}
            self.timeline = []
            self.diagnosis_finished = False

        async def complete(self, messages, **kwargs):
            if not messages[0]["content"].startswith("Role:"):
                return await super().complete(messages, **kwargs)
            payload = json.loads(messages[1]["content"])
            worker = payload["subtask"]["assigned_agent"]
            self.worker_inputs[worker] = payload
            self.timeline.append((worker, "started"))
            if worker == "diagnostic_agent":
                self.diagnosis_started.set()
                if independent:
                    await self.second_started.wait()
                if scenario == "upstream_timeout":
                    await asyncio.Event().wait()
                self.diagnosis_finished = True
                if scenario == "upstream_failure":
                    self.timeline.append((worker, "failed"))
                    raise ValueError("offline upstream failure")
                marker = diagnostic_marker
            else:
                await self.diagnosis_started.wait()
                assert self.diagnosis_finished is (not independent)
                self.second_started.set()
                marker = downstream_marker
            self.timeline.append((worker, "returned"))
            if worker == "diagnostic_agent" and scenario == "upstream_empty":
                return LLMResponse(content="")
            answer = f"A cautious draft: {marker}."
            if worker == "diagnostic_agent":
                answer = _with_private_block(
                    "" if scenario == "cleaned_empty" else answer, reasoning_format
                )
            return LLMResponse(
                content=json.dumps(
                    {
                        "answers": [
                            {"request_item_id": item, "answer": answer}
                            for item in payload["subtask"]["request_item_ids"]
                        ]
                    }
                )
            )

    llm = BarrierLLM()
    engine = NativeMedAgentEngine(
        RuntimeConfig(
            trace_dir=str(tmp_path),
            worker_timeout_seconds=0.05 if scenario == "upstream_timeout" else 5,
        ),
        llm=llm,
    )

    async def run():
        llm.diagnosis_started = asyncio.Event()
        llm.second_started = asyncio.Event()
        try:
            return await asyncio.wait_for(
                engine.analyze("Synthetic case only.", question, "handoff-audit"), timeout=5
            )
        finally:
            await engine.close()

    result = asyncio.run(run())
    events = read_trace(tmp_path / result["run_id"])
    by_id = {event["event_id"]: event for event in events}
    failure = scenario not in {"independent", "dependent"}
    assert set(llm.worker_inputs) == (
        {"diagnostic_agent"} if failure else {"diagnostic_agent", downstream}
    )
    if not failure:
        second_input = llm.worker_inputs[downstream]
        assert second_input["bounded_session_context"] == []
        assert (
            second_input["patient_facts"] == llm.worker_inputs["diagnostic_agent"]["patient_facts"]
        )
        assert "DO_NOT_HANDOFF_PRIVATE_REASONING" not in json.dumps(second_input)
        if independent:
            assert "upstream_handoffs" not in second_input
            assert diagnostic_marker not in json.dumps(second_input)
            assert llm.timeline.index((downstream, "started")) < llm.timeline.index(
                ("diagnostic_agent", "returned")
            )
        else:
            assert diagnostic_marker in json.dumps(second_input["upstream_handoffs"])
            assert llm.timeline.index((downstream, "started")) > llm.timeline.index(
                ("diagnostic_agent", "returned")
            )
    plan = next(event["payload"] for event in events if event["event_type"] == "plan_created")
    assert len(plan["subtasks"]) == 2
    assert plan["subtasks"][1]["depends_on"] == ([] if independent else ["diagnose"])
    worker_requests = [
        event
        for event in events
        if event["event_type"] == "llm_request" and event["stage"] == "worker"
    ]
    assert len(worker_requests) == (1 if failure else 2)
    policy = next(event for event in events if event["event_type"] == "capability_policy_applied")
    assert worker_requests[0]["parent_event_id"] == policy["event_id"]
    if independent:
        assert worker_requests[1]["parent_event_id"] == policy["event_id"]
        assert not any(event["event_type"].startswith("dependency") for event in events)
    elif not failure:
        assert by_id[worker_requests[1]["parent_event_id"]]["event_type"] == "dependency_ready"
        handoff = next(event for event in events if event["event_type"] == "worker_handoff")
        source = by_id[handoff["payload"]["source_event_id"]]
        assert source["event_type"] == "worker_draft"
        assert source["payload"]["subtask_id"] == "diagnose"
        assert handoff["parent_event_id"] == source["event_id"]
        assert handoff["payload"]["status"] == "success"
        assert handoff["payload"]["worker"] == "diagnostic_agent"
        assert "DO_NOT_HANDOFF_PRIVATE_REASONING" not in json.dumps(handoff["payload"])
    drafts = {event["agent"]: event for event in events if event["event_type"] == "worker_draft"}
    assert drafts[downstream]["payload"]["success"] is (not failure)
    if failure:
        assert drafts[downstream]["payload"]["worker_status"] == "dependency_blocked"
        assert drafts[downstream]["payload"]["provider_attempt_count"] == 0
        assert any(event["event_type"] == "dependency_blocked" for event in events)
        assert result["status"] == "incomplete"
        assert result["user_request_complete"] is False
        assert result["final_answer"] == ""
        assert not any(event["event_type"] == "synthesis_input" for event in events)
        assert result["trace"]["llm_calls"] == 2
    else:
        assert result["status"] == "completed"
        assert result["user_request_complete"] is True
        synthesis = next(
            event["payload"] for event in events if event["event_type"] == "synthesis_input"
        )
        assert diagnostic_marker in json.dumps(synthesis["worker_drafts"])
        assert downstream_marker in json.dumps(synthesis["worker_drafts"])
        assert diagnostic_marker in result["final_answer"]
        assert downstream_marker in result["final_answer"]
        assert "DO_NOT_HANDOFF_PRIVATE_REASONING" not in result["final_answer"]
        assert result["trace"]["llm_calls"] == 4
    assert replay(tmp_path / result["run_id"])["final_answer"] == result["final_answer"]
    print(
        json.dumps(
            {
                "scenario": scenario,
                "timeline": llm.timeline,
                "status": result["status"],
                "upstream_visible_to_second_worker": scenario == "dependent",
            }
        )
    )


@pytest.mark.parametrize("dependency", [["diagnose"], ["missing-task"], ["treat"]])
def test_planner_validates_dependency_metadata(dependency):
    raw = {
        "subtasks": [
            {
                "subtask_id": "diagnose",
                "description": "Diagnose",
                "assigned_agent": "diagnostic_agent",
            },
            {
                "subtask_id": "treat",
                "description": "Treat",
                "assigned_agent": "consultation_agent",
                "depends_on": dependency,
            },
        ]
    }
    if dependency == ["diagnose"]:
        parsed = Planner.parse(raw, "diagnostic_agent")
        assert parsed.fallback_reason is None
        assert parsed.subtasks[1].depends_on == dependency
    else:
        with pytest.raises(PlanDependencyError):
            Planner.parse(raw, "diagnostic_agent")


@pytest.mark.parametrize("bad", ["cycle", "duplicate_task", "string", "null", "duplicate_edge"])
def test_invalid_graphs_cannot_fall_back_to_parallel_execution(tmp_path, bad):
    first = {"subtask_id": "a", "description": "Assess", "assigned_agent": "diagnostic_agent"}
    second = {
        "subtask_id": "b",
        "description": "Assess",
        "assigned_agent": "diagnostic_agent",
        "depends_on": ["a"],
    }
    if bad == "cycle":
        first["depends_on"] = ["b"]
    elif bad == "duplicate_task":
        second["subtask_id"] = "a"
    else:
        second["depends_on"] = {"string": "a", "null": None, "duplicate_edge": ["a", "a"]}[bad]
    llm = ScriptedLLM([{"subtasks": [first, second]}])
    engine = NativeMedAgentEngine(RuntimeConfig(trace_dir=str(tmp_path)), llm=llm)

    async def run():
        try:
            with pytest.raises(PlanDependencyError):
                await engine.analyze("Synthetic", "diagnosis", "invalid")
        finally:
            await engine.close()

    asyncio.run(run())
    assert len(llm.requests) == 1
    (run_dir,) = tmp_path.iterdir()
    assert not any(event["event_type"] == "worker_draft" for event in read_trace(run_dir))


def test_contract_rewrites_preserve_same_role_dependency_identity_or_fail():
    contract = build_answer_contract("diagnosis")
    complexity = build_complexity_profile("diagnosis", contract)
    spec = build_request_spec("diagnosis")
    plan = Plan(
        [
            Subtask(
                "a",
                "First assessment",
                "diagnostic_agent",
                ["DIAGNOSIS_WITH_BASIS"],
                request_item_ids=["RQ1"],
            ),
            Subtask(
                "b",
                "Review first assessment",
                "diagnostic_agent",
                ["DIAGNOSIS_WITH_BASIS"],
                request_item_ids=["RQ1"],
                depends_on=["a"],
            ),
        ]
    )
    rewritten = apply_contract_policy(plan, contract, complexity, spec)
    assert [task.subtask_id for task in rewritten.subtasks] == ["a", "b"]
    assert rewritten.subtasks[1].depends_on == ["a"]
    assert "preserved_dependency_graph" in rewritten.policy_actions
    plan.subtasks[1].depends_on = []
    assert len(apply_contract_policy(plan, contract, complexity, spec).subtasks) == 1
    plan.subtasks[1].depends_on = ["a"]
    plan.subtasks[0].assigned_agent = "research_agent"  # Policy would remove this node.
    with pytest.raises(PlanDependencyError, match="remove tasks"):
        apply_contract_policy(plan, contract, complexity, spec)


@pytest.mark.parametrize("failure_node", [None, "branch", "root", "root_reasoning", "root_open"])
def test_multilevel_fanin_same_role_forward_references_and_order(tmp_path, failure_node):
    # Deliberately not topological: final output order must still match this plan.
    raw_tasks = [
        {"subtask_id": "tail", "depends_on": ["middle", "branch"]},
        {"subtask_id": "middle", "depends_on": ["root"]},
        {"subtask_id": "root", "depends_on": []},
        {"subtask_id": "branch", "depends_on": []},
    ]
    for task in raw_tasks:
        task.update(
            description="Assess diagnosis",
            assigned_agent="diagnostic_agent",
            deliverable_ids=["DIAGNOSIS_WITH_BASIS"],
            request_item_ids=["RQ1"],
        )

    class GraphLLM(ScriptedLLM):
        def __init__(self):
            super().__init__([{"subtasks": raw_tasks}, {"answers": []}])
            self.started = []
            self.finished = []
            self.inputs = {}

        async def complete(self, messages, **kwargs):
            if not messages[0]["content"].startswith("Role:"):
                return await super().complete(messages, **kwargs)
            payload = json.loads(messages[1]["content"])
            task = payload["subtask"]
            key = task["subtask_id"]
            assert all(source in self.finished for source in task["depends_on"])
            self.started.append(key)
            self.inputs[key] = payload
            handoffs = payload.get("upstream_handoffs", [])
            assert [item["source_subtask_id"] for item in handoffs] == task["depends_on"]
            for item in handoffs:
                assert item["request_item_answers"] == [
                    {
                        "request_item_id": "RQ1",
                        "answer": f"Public answer from {item['source_subtask_id']}.",
                    }
                ]
                assert item["status"] == "success"
            await asyncio.sleep(0)
            if key == failure_node:
                raise ValueError("offline branch failure")
            self.finished.append(key)
            answer = f"Public answer from {key}."
            if key == "root" and failure_node in {"root_reasoning", "root_open"}:
                answer = _with_private_block(
                    "", "think_open" if failure_node == "root_open" else "think"
                )
            return LLMResponse(
                content=json.dumps({"answers": [{"request_item_id": "RQ1", "answer": answer}]})
            )

    llm = GraphLLM()
    engine = NativeMedAgentEngine(RuntimeConfig(trace_dir=str(tmp_path)), llm=llm)

    async def run():
        try:
            return await asyncio.wait_for(engine.analyze("Synthetic", "diagnosis", "graph"), 5)
        finally:
            await engine.close()

    result = asyncio.run(run())
    events = read_trace(tmp_path / result["run_id"])
    workers = result["presentation"]["execution_summary"]["workers"]
    assert [item["subtask_id"] for item in workers] == [task["subtask_id"] for task in raw_tasks]
    if failure_node:
        assert "tail" not in llm.started
        if failure_node in {"root", "root_reasoning", "root_open"}:
            assert "middle" not in llm.started  # Failure propagates through blocked nodes.
        assert result["status"] == "incomplete"
        assert result["final_answer"] == ""
        # Other Workers cover RQ1, but a blocked task must not yield completed.
        coverage = next(
            event["payload"] for event in events if event["event_type"] == "request_coverage"
        )
        assert coverage["user_request_complete"] is True
        assert any(
            event["event_type"] == "error"
            and event["payload"].get("failure_reason") == "dependency_blocked"
            for event in events
        )
    else:
        assert llm.started[-1] == "tail"
        assert result["status"] == "completed"
        assert result["trace"]["llm_calls"] == 6  # One Planner, four Workers, one Synthesizer.
        assert "Public answer from tail." in result["final_answer"]
    assert replay(tmp_path / result["run_id"])["final_answer"] == result["final_answer"]


@pytest.mark.parametrize("bad", ["foreign_id", "duplicate", "missing_required", "empty_heading"])
def test_handoff_rejects_invalid_item_mappings_and_empty_content(bad):
    spec = build_request_spec("1. Give a diagnosis.\n2. Give a treatment plan.")
    task = Subtask("a", "Assess", "diagnostic_agent", request_item_ids=["RQ1", "RQ2"])
    answers = [
        RequestItemAnswer("RQ1", "Public answer."),
        RequestItemAnswer("RQ2", "Other answer."),
    ]
    if bad == "foreign_id":
        answers[1] = RequestItemAnswer("UNKNOWN_ID", "Answer.")
    elif bad == "duplicate":
        answers[1] = answers[0]
    elif bad == "missing_required":
        answers.pop()
    else:
        answers[1] = RequestItemAnswer("RQ2", "## Treatment")
    result = WorkerResult(
        "diagnostic_agent", "a", "Raw", request_item_answers=answers, trace_event_id="source-event"
    )
    assert build_handoff(task, result, spec) is None


@pytest.mark.parametrize("reasoning_format", ["analysis", "think", "analysis_open", "think_open"])
def test_handoff_preserves_multiple_clean_answers_and_explicit_provenance(reasoning_format):
    spec = build_request_spec("1. Give a diagnosis.\n2. Give a treatment plan.")
    task = Subtask("a", "Assess", "diagnostic_agent", request_item_ids=["RQ1", "RQ2"])
    result = WorkerResult(
        "diagnostic_agent",
        "a",
        "Raw not transferred",
        request_item_answers=[
            RequestItemAnswer("RQ1", _with_private_block("Public diagnosis.", reasoning_format)),
            RequestItemAnswer("RQ2", _with_private_block("Public treatment.", reasoning_format)),
        ],
        trace_event_id="source-event",
    )
    handoff = build_handoff(task, result, spec)
    assert handoff is not None
    assert handoff.source_event_id == "source-event"
    assert handoff.source_subtask_id == "a"
    assert handoff.request_item_answers == (
        RequestItemAnswer("RQ1", "Public diagnosis."),
        RequestItemAnswer("RQ2", "Public treatment."),
    )
    assert "DO_NOT_HANDOFF_PRIVATE_REASONING" not in json.dumps(handoff.to_dict())
    assert "Raw not transferred" not in json.dumps(handoff.to_dict())


def test_broken_dependency_json_is_not_silently_replaced_with_legacy_plan():
    with pytest.raises(PlanDependencyError):
        Planner.parse('{"subtasks": [{"depends_on": ["a"]}', "diagnostic_agent")
    legacy = Planner.parse("not-json", "diagnostic_agent")
    assert legacy.fallback_reason
    assert legacy.subtasks[0].depends_on == []


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("<think>secret</think>Public.", "Public."),
        ("Before.<analysis>secret</analysis>After.", "Before.After."),
        ("Before.<think>secret", "Before."),
        ("Before.<analysis>secret", "Before."),
        ("<think>secret</think>", ""),
        ("<analysis>secret", ""),
        ("<THINK>secret\nmore</THINK>中文公开答案。", "中文公开答案。"),
        ("A<think>outer<analysis>inner</analysis>tail</think>B", "AB"),
        ("A<think>outer<think>inner</think>tail</think>B", "AB"),
        ("A<think>outer<analysis>inner</think>tail", "A"),
        ("A<analysis>x</analysis>B<think>y</think>C<analysis>z", "ABC"),
        ("Public.\nPrivate reasoning: secret", "Public."),
        ("Public <thinker> literal.", "Public <thinker> literal."),
    ],
)
def test_reasoning_sanitizer_removes_blocks_not_just_tags(raw, expected):
    assert sanitize_answer(raw) == expected
    assert sanitize_answer(expected) == expected


@pytest.mark.parametrize("reasoning_format", ["analysis", "think", "analysis_open", "think_open"])
@pytest.mark.parametrize("pure_second", [False, True])
def test_multi_answer_handoff_checks_every_required_answer_before_downstream(
    tmp_path, reasoning_format, pure_second
):
    question = "1. Give a diagnosis.\n2. Give a differential diagnosis.\n3. Give a treatment plan."
    plan = {
        "subtasks": [
            {
                "subtask_id": "diagnose",
                "description": "Assess primary and alternative causes.",
                "assigned_agent": "diagnostic_agent",
                "deliverable_ids": ["DIAGNOSIS_WITH_BASIS", "DIFFERENTIAL_DIAGNOSIS"],
                "request_item_ids": ["RQ1", "RQ2"],
            },
            {
                "subtask_id": "treat",
                "description": "Use the upstream assessment.",
                "assigned_agent": "consultation_agent",
                "deliverable_ids": ["TREATMENT_PLAN"],
                "request_item_ids": ["RQ3"],
                "depends_on": ["diagnose"],
            },
        ]
    }
    public = ["Public primary assessment.", "Public alternative assessment."]
    llm = ScriptedLLM(
        [
            plan,
            {
                "answers": [
                    {
                        "request_item_id": "RQ1",
                        "answer": _with_private_block(public[0], reasoning_format),
                    },
                    {
                        "request_item_id": "RQ2",
                        "answer": _with_private_block(
                            "" if pure_second else public[1], reasoning_format
                        ),
                    },
                ]
            },
            {"answers": [{"request_item_id": "RQ3", "answer": "Public care assessment."}]},
            {"answers": []},
        ]
    )
    engine = NativeMedAgentEngine(RuntimeConfig(trace_dir=str(tmp_path)), llm=llm)

    async def run():
        try:
            return await engine.analyze("Synthetic case only.", question, "multi-answer")
        finally:
            await engine.close()

    result = asyncio.run(run())
    events = read_trace(tmp_path / result["run_id"])
    handoffs = [event for event in events if event["event_type"] == "worker_handoff"]
    if pure_second:
        assert len(llm.requests) == result["trace"]["llm_calls"] == 2
        assert handoffs == []
        assert result["status"] == "incomplete"
        assert result["user_request_complete"] is False
        assert result["final_answer"] == ""
        blocked = next(event for event in events if event["event_type"] == "dependency_blocked")
        assert blocked["payload"]["blocked_by"] == ["diagnose"]
    else:
        assert len(llm.requests) == result["trace"]["llm_calls"] == 4
        assert result["status"] == "completed"
        assert len(handoffs) == 1
        payload = handoffs[0]["payload"]
        by_id = {event["event_id"]: event for event in events}
        assert payload["source_subtask_id"] == "diagnose"
        assert payload["source_event_id"] == handoffs[0]["parent_event_id"]
        assert by_id[payload["source_event_id"]]["payload"]["subtask_id"] == "diagnose"
        assert payload["request_item_answers"] == [
            {"request_item_id": f"RQ{i + 1}", "answer": text} for i, text in enumerate(public)
        ]
        assert "DO_NOT_HANDOFF_PRIVATE_REASONING" not in json.dumps(payload)
        downstream_prompt = llm.requests[2]["messages"]
        assert "DO_NOT_HANDOFF_PRIVATE_REASONING" not in json.dumps(downstream_prompt)
        downstream = json.loads(downstream_prompt[1]["content"])
        assert (
            downstream["upstream_handoffs"][0]["request_item_answers"]
            == payload["request_item_answers"]
        )
        for index, text in enumerate(public, 1):
            assert result["final_request_item_answers"][f"RQ{index}"] == text
        assert "DO_NOT_HANDOFF_PRIVATE_REASONING" not in result["final_answer"]
