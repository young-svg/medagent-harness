"""B2 regressions: history-only estimated budgets, shared injection and real writes."""

import asyncio
import json

import pytest

from medagent.llm import ScriptedLLM
from medagent.memory.session import SessionMemory, estimate_history_tokens
from medagent.observability.replay import read_trace
from medagent.runtime.config import RuntimeConfig
from medagent.runtime.native_engine import NativeMedAgentEngine


def _cost(messages):
    """Diagnostic heuristic only, NOT provider token usage or a strict bound."""
    serialized = json.dumps(messages, ensure_ascii=False)
    return {
        "messages": len(messages),
        "serialized_chars": len(serialized),
        "serialized_utf8_bytes": len(serialized.encode("utf-8")),
        "estimated_history_tokens": estimate_history_tokens(messages),
    }


@pytest.mark.parametrize("unit", ["history word ", "历史病情资料。", '🩺🙂\n"evidence"'])
@pytest.mark.parametrize("budget", [4096, 0, None])
def test_long_history_obeys_message_and_estimated_budget_limits(unit, budget):
    memory = SessionMemory(recent_limit=4, token_budget=budget)
    contents = []
    reports = []
    for turn in range(3):
        for role in ("user", "assistant"):
            content = f"turn-{turn}-{role}: " + unit * 2000
            contents.append({"role": role, "content": content.strip()})
            memory.add("long", role, content)
        context, metadata = memory.context_with_metadata("long", "current request")
        history = context[:-1]
        assert history == (contents[-3:] if budget is None else [])
        assert metadata["estimated_history_tokens"] == estimate_history_tokens(history)
        assert metadata["retained_history_messages"] == len(history)
        assert metadata["trimmed_history_messages"] == min(len(contents), 4) - len(history)
        if budget is not None:
            assert metadata["estimated_history_tokens"] <= budget
        assert context[-1] == {"role": "user", "content": "current request"}
        reports.append(_cost(history))
    assert len(memory._messages["long"]) == 4
    if budget is None:
        assert reports[-1]["estimated_history_tokens"] > 4096


@pytest.mark.parametrize("unit", ["historic evidence ", "既往病情资料。"])
@pytest.mark.parametrize("budget", [4096, 0, None])
def test_long_multiturn_history_reaches_actual_planner_and_worker(tmp_path, unit, budget):
    plan = {
        "subtasks": [
            {
                "subtask_id": "t1",
                "description": "Assess current request",
                "assigned_agent": "diagnostic_agent",
            }
        ]
    }
    descriptions = [f"CURRENT_{index}: " + unit * 1000 for index in range(3)]
    drafts = [f"ANSWER_{index}: " + unit * 1000 for index in range(3)]
    llm = ScriptedLLM([value for draft in drafts for value in (plan, draft)])
    memory = SessionMemory(recent_limit=4, token_budget=budget)
    engine = NativeMedAgentEngine(RuntimeConfig(trace_dir=str(tmp_path)), llm=llm, memory=memory)
    question = "Assess the case"

    async def run():
        results = []
        try:
            for description in descriptions:
                results.append(await engine.analyze(description, question, "long-session"))
            return results
        finally:
            await engine.close()

    results = asyncio.run(run())
    expected_stored = []
    reports = []
    for index, result in enumerate(results):
        assert result["status"] == "completed"
        events = read_trace(tmp_path / result["run_id"])
        read = next(event["payload"] for event in events if event["event_type"] == "memory_read")
        history = read["injected_messages"]
        assert history == (expected_stored[-3:] if budget is None else [])
        assert read["history_token_budget"] == budget
        assert read["estimated_history_tokens"] == estimate_history_tokens(history)
        assert read["retained_history_messages"] == len(history)
        assert read["trimmed_history_messages"] == len(expected_stored) - len(history)
        assert read["current_input"]["content"] == f"{descriptions[index]}\nQuestion: {question}"
        planner_text = llm.requests[index * 2]["messages"][1]["content"]
        prefix = "Bounded session context (background only): "
        planner_history = json.loads(
            planner_text.split(prefix, 1)[1].split("\nCurrent request (authoritative): ", 1)[0]
        )
        assert planner_history == history
        worker = json.loads(llm.requests[index * 2 + 1]["messages"][1]["content"])
        assert worker["bounded_session_context"] == history
        # Current authoritative input and task metadata are not history budget candidates.
        assert worker["current_context"] == {
            "description": descriptions[index],
            "question": question,
        }
        assert worker["subtask"]["request_item_ids"]
        assert worker["request_spec"]["items"]
        assert worker["contract"]["requested_deliverables"]
        assert worker["patient_facts"]
        assert all(descriptions[index] not in message["content"] for message in history)
        reports.append(_cost(history))
        expected_stored.extend(
            [
                {"role": "user", "content": f"{descriptions[index]}\nQuestion: {question}"},
                {"role": "assistant", "content": result["final_answer"]},
            ]
        )
        expected_stored = expected_stored[-4:]
    assert len(llm.requests) == 6
    assert llm.closed
    assert reports[0]["messages"] == 0
    assert reports[1]["messages"] == (2 if budget is None else 0)
    assert reports[2]["messages"] == (3 if budget is None else 0)
    assert "CURRENT_0" not in json.dumps(history, ensure_ascii=False)
    # Closing the engine closes clients, but does not clear its injected Memory instance.
    assert memory.context("long-session", "next")[:-1] == (
        expected_stored[-3:] if budget is None else []
    )
    assert memory.context("other-session", "isolated") == [{"role": "user", "content": "isolated"}]


@pytest.mark.parametrize("budget", [0, 4096, None])
def test_recent_limit_one_never_injects_history(budget):
    memory = SessionMemory(recent_limit=1, token_budget=budget)
    memory.add("s", "user", "old input")
    memory.add("s", "assistant", "old answer")
    context = memory.context("s", "current input")
    assert context == [{"role": "user", "content": "current input"}]


def test_isolation_exact_dedup_and_clear_semantics():
    memory = SessionMemory(recent_limit=10)
    memory.add("a", "user", " repeated ")
    memory.add("a", "user", "repeated")
    memory.add("a", "assistant", "reply")
    memory.add("a", "user", "repeated")  # Same role/content in last two: suppressed.
    assert len(memory._messages["a"]) == 2
    memory.add("a", "user", "other")
    memory.add("a", "assistant", "other reply")
    memory.add("a", "user", "repeated")  # Outside the last two: stored again.
    assert len(memory._messages["a"]) == 5
    memory.add("b", "user", "session B only")
    memory.add("a", "assistant", "repeated")  # Different role is not a duplicate.
    context = memory.context("a", " repeated ")
    assert sum(item == {"role": "user", "content": "repeated"} for item in context) == 1
    assert {"role": "assistant", "content": "repeated"} in context
    assert "session B only" not in json.dumps(context)
    context[0]["content"] = "caller mutation"
    assert "caller mutation" not in json.dumps(memory.context("a", "new"))
    memory.clear("a")
    assert memory.context("a", "fresh") == [{"role": "user", "content": "fresh"}]
    assert memory.context("b", "fresh")[0]["content"] == "session B only"


def test_disabled_memory_suppresses_reads_and_writes_but_does_not_clear(tmp_path):
    memory = SessionMemory()
    memory.add("s", "user", "OLD_HISTORY_MARKER")
    memory.enabled = False
    memory.add("s", "user", "IGNORED_WRITE_MARKER")
    assert memory.context("s", "CURRENT_INPUT") == [{"role": "user", "content": "CURRENT_INPUT"}]
    llm = ScriptedLLM(
        [
            {"subtasks": [{"description": "Assess", "assigned_agent": "diagnostic_agent"}]},
            "A cautious answer.",
        ]
    )
    engine = NativeMedAgentEngine(RuntimeConfig(trace_dir=str(tmp_path)), llm=llm, memory=memory)

    async def run():
        try:
            return await engine.analyze("CURRENT_INPUT", "Assess", "s")
        finally:
            await engine.close()

    result = asyncio.run(run())
    events = read_trace(tmp_path / result["run_id"])
    read = next(event["payload"] for event in events if event["event_type"] == "memory_read")
    assert read["injected_messages"] == []
    assert "OLD_HISTORY_MARKER" not in json.dumps(llm.requests)
    assert len(memory._messages["s"]) == 1
    write = next(event["payload"] for event in events if event["event_type"] == "memory_write")
    assert write["messages_added"] == 0
    assert read["memory_enabled"] is False
    assert read["estimated_history_tokens"] == read["retained_history_messages"] == 0
    memory.enabled = True
    assert memory.context("s", "new")[0]["content"] == "OLD_HISTORY_MARKER"
    assert "IGNORED_WRITE_MARKER" not in json.dumps(memory.context("s", "new"))


@pytest.mark.parametrize("text", ["short English", "中文病情。", '🩺🙂\n"quote"\\'])
def test_estimator_accounts_for_utf8_json_roles_and_framing(text):
    messages = [{"role": "assistant", "content": text}]
    assert estimate_history_tokens([]) == 0
    assert estimate_history_tokens(messages) == (
        len(json.dumps(messages, ensure_ascii=False).encode("utf-8")) + 8
    )
    assert estimate_history_tokens(messages) > len(text.encode("utf-8"))


def test_oldest_whole_messages_are_removed_after_count_limit():
    contents = [{"role": "user", "content": f"message-{i}"} for i in range(5)]
    budget = estimate_history_tokens(contents[-2:])
    memory = SessionMemory(recent_limit=4, token_budget=budget)
    for message in contents:
        memory.add("s", **message)
    context, metadata = memory.context_with_metadata("s", "current " * 10000)
    assert context[:-1] == contents[-2:]
    assert context[-1]["content"] == ("current " * 10000).strip()
    assert metadata["available_history_messages"] == 4  # Storage already evicted oldest.
    assert metadata["count_trimmed_history_messages"] == 1
    assert metadata["budget_trimmed_history_messages"] == 1
    assert metadata["trimmed_history_messages"] == 2
    assert metadata["estimated_history_tokens"] == budget
    # One below the exact boundary removes a whole message, not part of its content.
    memory.token_budget = budget - 1
    assert memory.context("s", "new")[:-1] == contents[-1:]
    # A too-large newest message must not cause older, stale history to be selected instead.
    memory.add("s", "assistant", "too large " * 10000)
    assert memory.context("s", "new")[:-1] == []


def test_partial_history_snapshot_is_shared_by_planner_and_all_workers(tmp_path, monkeypatch):
    memory = SessionMemory(recent_limit=4)
    stored = [
        {"role": "user", "content": "OLD_USER"},
        {"role": "assistant", "content": "OLD_ANSWER"},
        {"role": "user", "content": "RECENT_USER"},
        {"role": "assistant", "content": "RECENT_ANSWER"},
    ]
    for message in stored:
        memory.add("s", **message)
    memory.token_budget = estimate_history_tokens(stored[-2:])
    reads = []
    original_read = memory.context_with_metadata

    def read_once(*args):
        value = original_read(*args)
        reads.append(value)
        return value

    monkeypatch.setattr(memory, "context_with_metadata", read_once)
    llm = ScriptedLLM(
        [
            {
                "subtasks": [
                    {"description": "Diagnose", "assigned_agent": "diagnostic_agent"},
                    {"description": "Treat", "assigned_agent": "consultation_agent"},
                ]
            },
            "A cautious diagnostic assessment.",
            "A cautious management assessment.",
            {"answers": []},
        ]
    )
    engine = NativeMedAgentEngine(RuntimeConfig(trace_dir=str(tmp_path)), llm=llm, memory=memory)

    async def run():
        try:
            return await engine.analyze(
                "CURRENT_FACTS " * 1000, "diagnosis and treatment plan", "s"
            )
        finally:
            await engine.close()

    result = asyncio.run(run())
    assert len(reads) == 1
    assert result["status"] == "completed"
    assert len(llm.requests) == 4
    history = reads[0][0][:-1]
    assert history == stored[-2:]
    planner_text = llm.requests[0]["messages"][1]["content"]
    assert "Bounded session context (background only): " + json.dumps(history) in planner_text
    for request in llm.requests[1:3]:
        worker = json.loads(request["messages"][1]["content"])
        assert worker["bounded_session_context"] == history
        assert worker["current_context"]["description"] == "CURRENT_FACTS " * 1000
    read = next(
        event["payload"]
        for event in read_trace(tmp_path / result["run_id"])
        if event["event_type"] == "memory_read"
    )
    assert read["injected_messages"] == history
    assert read["estimated_history_tokens"] == memory.token_budget
    assert read["count_trimmed_history_messages"] == read["budget_trimmed_history_messages"] == 1


def test_memory_write_trace_counts_insertions_not_attempts_or_net_size(tmp_path):
    plan = {"subtasks": [{"description": "Assess", "assigned_agent": "diagnostic_agent"}]}
    llm = ScriptedLLM([value for _ in range(3) for value in (plan, "Identical cautious answer.")])
    memory = SessionMemory(recent_limit=2)
    engine = NativeMedAgentEngine(RuntimeConfig(trace_dir=str(tmp_path)), llm=llm, memory=memory)

    async def run():
        try:
            return [
                await engine.analyze(description, "Assess", "s")
                for description in ("same case", "same case", "changed case")
            ]
        finally:
            await engine.close()

    results = asyncio.run(run())
    counts = [
        next(
            event["payload"]["messages_added"]
            for event in read_trace(tmp_path / result["run_id"])
            if event["event_type"] == "memory_write"
        )
        for result in results
    ]
    assert counts == [2, 0, 1]
    assert len(memory._messages["s"]) == 2
    assert memory.add("s", "user", "  ") is False


@pytest.mark.parametrize(
    "raw, expected", [("4096", 4096), ("0", 0), ("None", None), (" none ", None), ("12", 12)]
)
def test_runtime_memory_budget_configuration(tmp_path, monkeypatch, raw, expected):
    monkeypatch.setenv("MEDAGENT_MEMORY_TOKEN_BUDGET", raw)
    config = RuntimeConfig.from_env()
    assert config.memory_token_budget == expected
    assert config.public_dict()["memory_token_budget"] == expected
    # Avoid environment-dependent model/retrieval settings in this unit check.
    engine = NativeMedAgentEngine(
        RuntimeConfig(trace_dir=str(tmp_path), memory_token_budget=expected)
    )
    assert engine.memory.token_budget == expected
    asyncio.run(engine.close())


@pytest.mark.parametrize("invalid", [-1, 1.5, True, "4096"])
def test_invalid_memory_budgets_are_rejected(invalid):
    with pytest.raises(ValueError):
        SessionMemory(token_budget=invalid)
    with pytest.raises(ValueError):
        RuntimeConfig(memory_token_budget=invalid)


def test_defaults_and_explicit_memory_override_are_compatible(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDAGENT_MEMORY_TOKEN_BUDGET", raising=False)
    assert SessionMemory().token_budget == RuntimeConfig.from_env().memory_token_budget == 4096
    memory = SessionMemory(token_budget=None, enabled=False)
    engine = NativeMedAgentEngine(
        RuntimeConfig(trace_dir=str(tmp_path), memory_token_budget=0), memory=memory
    )
    assert engine.memory is memory
    assert engine.memory.token_budget is None
    assert engine.memory.enabled is False
    asyncio.run(engine.close())
