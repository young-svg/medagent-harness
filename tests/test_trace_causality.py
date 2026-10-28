import asyncio
import json

import pytest

from medagent.agents.consultation import CONSULTATION_AGENT
from medagent.agents.diagnostic import DIAGNOSTIC_AGENT
from medagent.context.contract import build_answer_contract
from medagent.context.evidence_ledger import EvidenceLedger
from medagent.context.request_spec import build_request_spec
from medagent.llm import LLMResponse, ScriptedLLM, ToolCall
from medagent.observability.replay import read_trace, replay, trace_summary
from medagent.observability.tracer import TraceRecorder
from medagent.planning.complexity import build_complexity_profile, build_response_profile
from medagent.planning.models import Subtask
from medagent.runtime.agent_loop import AgentLoop
from medagent.runtime.config import RuntimeConfig
from medagent.runtime.engine import EngineExecutionError
from medagent.runtime.native_engine import NativeMedAgentEngine
from medagent.skills.loader import load_public_skills
from medagent.tools.registry import ToolRegistry


def test_concurrent_events_without_known_parent_are_independent(tmp_path):
    trace = TraceRecorder(tmp_path)

    async def run():
        started = asyncio.Event()
        other_started = asyncio.Event()

        async def first():
            request = trace.record("llm_request", agent="diagnostic_agent")
            started.set()
            await other_started.wait()
            trace.record("llm_response", agent="diagnostic_agent", parent_event_id=request)

        async def second():
            await started.wait()
            trace.record("llm_request", agent="consultation_agent")
            other_started.set()

        await asyncio.gather(first(), second())

    asyncio.run(run())
    events = read_trace(trace.run_dir)
    assert events[1]["parent_event_id"] is None
    assert events[2]["parent_event_id"] == events[0]["event_id"]


@pytest.mark.parametrize("mode", ["plain", "tool", "invalid_tool", "failure", "retry", "length"])
def test_concurrent_worker_causal_chains_and_replay(tmp_path, mode):
    trace = TraceRecorder(tmp_path)
    root = trace.record("capability_policy_applied", stage="routing")
    question = "Assess safely"
    spec = build_request_spec(question)
    contract = build_answer_contract(question)
    complexity = build_complexity_profile(question, contract)
    skills = load_public_skills()
    registry = ToolRegistry()

    async def tool(symptoms):
        await asyncio.sleep(0)
        return {"value": symptoms, "_trace_retrieval": {"query": symptoms}}

    registry.register("assess_risk", tool)

    class ConcurrentLLM:
        def __init__(self):
            self.both_started = asyncio.Event()
            self.counts = {}

        async def complete(self, messages, **kwargs):
            task = json.loads(messages[1]["content"])["subtask"]
            worker = task["assigned_agent"]
            count = self.counts.get(worker, 0) + 1
            self.counts[worker] = count
            if count == 1:
                if len(self.counts) == 2:
                    self.both_started.set()
                await self.both_started.wait()
                # Yield with both requests outstanding, without any real API.
                await asyncio.sleep(0)
                if mode == "failure":
                    raise ValueError("offline provider failure")
                if mode == "retry":
                    raise TimeoutError("offline timeout")
                if mode == "length":
                    return LLMResponse(content="", finish_reason="length")
                if mode in {"tool", "invalid_tool"}:
                    arguments = {"symptoms": "synthetic"} if mode == "tool" else {}
                    return LLMResponse(tool_calls=[ToolCall(worker, "assess_risk", arguments)])
            return LLMResponse(
                content=json.dumps(
                    {
                        "answers": [
                            {"request_item_id": item, "answer": "A cautious completed draft."}
                            for item in task["request_item_ids"]
                        ]
                    }
                )
            )

    async def run():
        llm = ConcurrentLLM()
        loop = AgentLoop(llm, registry)
        results = await asyncio.wait_for(
            asyncio.gather(
                *[
                    loop.execute(
                        agent,
                        Subtask(
                            agent.agent_id,
                            question,
                            agent.agent_id,
                            request_item_ids=spec.required_item_ids,
                        ),
                        spec,
                        contract,
                        EvidenceLedger(),
                        {"question": question},
                        [],
                        skills[agent.agent_id],
                        trace,
                        complexity,
                        build_response_profile(complexity),
                        parent_event_id=root,
                    )
                    for agent in (DIAGNOSTIC_AGENT, CONSULTATION_AGENT)
                ]
            ),
            timeout=5,
        )
        return llm, results

    llm, results = asyncio.run(run())
    assert all(result.success == (mode != "failure") for result in results)
    expected_calls = 1 if mode in {"plain", "failure"} else 2
    assert set(llm.counts.values()) == {expected_calls}
    events = read_trace(trace.run_dir)
    by_id = {event["event_id"]: event for event in events}
    assert len(by_id) == len(events)
    for event in events:
        cursor = event
        while cursor["parent_event_id"] is not None:
            parent = by_id[cursor["parent_event_id"]]
            assert parent["agent"] in {None, event["agent"]}
            assert events.index(parent) < events.index(cursor)
            cursor = parent
    for worker in llm.counts:
        own = [event for event in events if event["agent"] == worker]
        requests = [event for event in own if event["event_type"] == "llm_request"]
        assert requests[0]["parent_event_id"] == root
        for response in (event for event in own if event["event_type"] == "llm_response"):
            assert by_id[response["parent_event_id"]]["event_type"] == "llm_request"
        draft = next(event for event in own if event["event_type"] == "worker_draft")
        if mode == "failure":
            assert draft["parent_event_id"] == root
        else:
            assert by_id[draft["parent_event_id"]]["event_type"] == "llm_response"
        if mode in {"tool", "invalid_tool"}:
            result = next(event for event in own if event["event_type"] == "tool_result")
            assert requests[1]["parent_event_id"] == result["event_id"]
            parent = by_id[result["parent_event_id"]]
            if mode == "tool":
                assert parent["event_type"] == "retrieval_result"
                parent = by_id[parent["parent_event_id"]]
                assert parent["event_type"] == "retrieval_query"
                parent = by_id[parent["parent_event_id"]]
            else:
                assert "error" in result["payload"]["result"]
            assert parent["event_type"] == "tool_call"
            assert parent["parent_event_id"] == next(
                event["event_id"] for event in own if event["event_type"] == "llm_response"
            )
    trace.record("final_answer", {"answer": "Offline replay body."})
    trace.finish("incomplete" if mode == "failure" else "completed")
    persisted = read_trace(trace.run_dir)
    assert persisted == [event.to_dict() for event in trace.events]
    summary = trace.summary()
    replayed = replay(trace.run_dir)
    assert replayed["final_answer"] == "Offline replay body."
    assert replayed["summary"] == trace_summary(trace.run_dir)
    assert replayed["summary"]["status"] == summary["status"]
    assert replayed["summary"]["event_count"] == summary["event_count"]
    assert summary["llm_calls"] == expected_calls * 2
    assert summary["tool_calls"] == (2 if mode in {"tool", "invalid_tool"} else 0)


@pytest.mark.parametrize("mode", ["single", "multi", "timeout"])
def test_engine_worker_fanout_and_failure_parents(tmp_path, monkeypatch, mode):
    agents = ["diagnostic_agent"]
    if mode != "single":
        agents.append("research_agent")
    llm = ScriptedLLM(
        [
            {
                "subtasks": [
                    {"subtask_id": f"t{index}", "description": "Assess", "assigned_agent": agent}
                    for index, agent in enumerate(agents)
                ]
            },
            *["A cautious assessment based on the available information." for _ in agents],
            "A cautious combined assessment.",
        ]
    )
    engine = NativeMedAgentEngine(RuntimeConfig(trace_dir=str(tmp_path)), llm=llm)
    if mode == "timeout":

        async def timed_out(*args, **kwargs):
            await asyncio.sleep(0)
            raise TimeoutError("offline worker timeout")

        monkeypatch.setattr(AgentLoop, "execute", timed_out)

    async def run():
        try:
            if mode == "timeout":
                with pytest.raises(EngineExecutionError, match="all workers failed"):
                    await engine.analyze("Synthetic symptom", "diagnosis with research evidence")
            else:
                question = "diagnosis" if mode == "single" else "diagnosis with research evidence"
                result = await engine.analyze("Synthetic symptom", question)
                assert replay(tmp_path / result["run_id"])["final_answer"] == result["final_answer"]
        finally:
            await engine.close()

    asyncio.run(run())
    (run_dir,) = tmp_path.iterdir()
    events = read_trace(run_dir)
    by_id = {event["event_id"]: event for event in events}
    policy = next(event for event in events if event["event_type"] == "capability_policy_applied")
    route = by_id[policy["parent_event_id"]]
    assert route["event_type"] == "route_selected"
    assert by_id[route["parent_event_id"]]["event_type"] == "plan_created"
    if mode == "timeout":
        failures = [event for event in events if event["event_type"] == "worker_draft"]
        assert len(failures) == 2
        for draft in failures:
            error = by_id[draft["parent_event_id"]]
            assert error["event_type"] == "error"
            assert error["agent"] == draft["agent"]
            assert error["parent_event_id"] == policy["event_id"]
    else:
        requests = [
            event
            for event in events
            if event["event_type"] == "llm_request" and event["stage"] == "worker"
        ]
        assert len(requests) == len(agents)
        assert all(event["parent_event_id"] == policy["event_id"] for event in requests)
        assert len(llm.requests) == (2 if mode == "single" else 4)
