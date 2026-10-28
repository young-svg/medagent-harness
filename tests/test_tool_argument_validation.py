from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from unittest.mock import AsyncMock, Mock

import pytest

from medagent.agents.diagnostic import DIAGNOSTIC_AGENT
from medagent.context.contract import build_answer_contract
from medagent.context.evidence_ledger import EvidenceLedger
from medagent.context.request_spec import build_request_spec
from medagent.llm import LLMResponse, ScriptedLLM, ToolCall
from medagent.observability.replay import read_trace
from medagent.observability.tracer import TraceRecorder
from medagent.planning.complexity import build_complexity_profile, build_response_profile
from medagent.planning.models import Subtask
from medagent.runtime.agent_loop import AgentLoop
from medagent.skills.loader import load_public_skills
from medagent.tools.registry import VISIBLE_TOOLS, ToolRegistry
from medagent.tools.schemas import TOOL_SCHEMAS

WORKER = "diagnostic_agent"
TOOL = "assess_risk"
INVALID_ARGUMENTS = [
    pytest.param(None, "expected an object", id="null-object"),
    pytest.param([], "expected an object", id="array"),
    pytest.param("private-value", "expected an object", id="string-object"),
    pytest.param(7, "expected an object", id="integer-object"),
    pytest.param(True, "expected an object", id="boolean-object"),
    pytest.param({}, "missing required field 'symptoms'", id="missing"),
    pytest.param({"symptoms": None}, "must be a string", id="null-field"),
    pytest.param({"symptoms": 7}, "must be a string", id="integer-field"),
    pytest.param({"symptoms": True}, "must be a string", id="boolean-field"),
    pytest.param({"symptoms": []}, "must be a string", id="array-field"),
    pytest.param({"symptoms": {}}, "must be a string", id="object-field"),
    pytest.param(
        {"symptoms": "private-value", "private-key": "private-value"},
        "additional fields are not allowed",
        id="extra-field",
    ),
]


@pytest.fixture
def bounded_schema(monkeypatch):
    # Production schemas currently impose no string bounds. Test explicit bounds
    # through the same schema object exposed to the LLM, without changing policy.
    schema = deepcopy(TOOL_SCHEMAS[TOOL])
    schema["function"]["parameters"]["properties"]["symptoms"].update(
        minLength=1, maxLength=8
    )
    monkeypatch.setitem(TOOL_SCHEMAS, TOOL, schema)
    return schema


@pytest.mark.parametrize(
    ("worker", "name"),
    [(worker, name) for worker, names in VISIBLE_TOOLS.items() for name in sorted(names)],
)
def test_valid_arguments_follow_each_visible_tool_schema(worker, name):
    registry = ToolRegistry()
    handler = Mock(return_value={"ok": True})
    registry.register(name, handler)
    schema = registry.schemas_for(worker)[0]
    assert schema is TOOL_SCHEMAS[name]
    parameters = schema["function"]["parameters"]
    assert parameters["additionalProperties"] is False
    field = parameters["required"][0]
    arguments = {field: "  clinical text \u75c7\u72b6  "}

    assert asyncio.run(registry.execute(worker, name, arguments)) == {"ok": True}
    handler.assert_called_once_with(**arguments)
    assert registry.calls_for(worker) == 1


@pytest.mark.parametrize(("arguments", "message"), INVALID_ARGUMENTS)
def test_invalid_arguments_are_rejected_before_handler(arguments, message):
    registry = ToolRegistry()
    handler = Mock()
    registry.register(TOOL, handler)

    with pytest.raises(TypeError, match=message) as error:
        asyncio.run(registry.execute(WORKER, TOOL, arguments))

    handler.assert_not_called()
    assert registry.calls_for(WORKER) == 1
    assert "private-value" not in str(error.value)
    assert "private-key" not in str(error.value)


@pytest.mark.parametrize("value", ["", "   ", "x" * 65536], ids=["empty", "spaces", "long"])
def test_unconstrained_strings_are_preserved(value):
    registry = ToolRegistry()
    handler = Mock(return_value="ok")
    registry.register(TOOL, handler)

    assert asyncio.run(registry.execute(WORKER, TOOL, {"symptoms": value})) == "ok"
    handler.assert_called_once_with(symptoms=value)


@pytest.mark.parametrize("value", ["a", "12345678", "\u75c7" * 8, " "])
def test_declared_length_boundaries_are_inclusive(bounded_schema, value):
    registry = ToolRegistry()
    handler = AsyncMock(return_value="ok")
    registry.register(TOOL, handler)
    assert registry.schemas_for(WORKER) == [bounded_schema]

    assert asyncio.run(registry.execute(WORKER, TOOL, {"symptoms": value})) == "ok"
    handler.assert_awaited_once_with(symptoms=value)
    assert registry.calls_for(WORKER) == 1


@pytest.mark.parametrize(("value", "message"), [("", "minLength"), ("x" * 9, "maxLength")])
def test_declared_length_violations_do_not_call_handler(bounded_schema, value, message):
    registry = ToolRegistry()
    handler = Mock()
    registry.register(TOOL, handler)

    with pytest.raises(TypeError, match=message):
        asyncio.run(registry.execute(WORKER, TOOL, {"symptoms": value}))

    handler.assert_not_called()
    assert registry.calls_for(WORKER) == 1


def test_schema_defaults_are_not_silently_tightened(bounded_schema):
    parameters = bounded_schema["function"]["parameters"]
    parameters.pop("required")
    parameters.pop("additionalProperties")
    registry = ToolRegistry()
    handler = Mock(return_value="ok")
    registry.register(TOOL, handler)

    assert asyncio.run(registry.execute(WORKER, TOOL, {})) == "ok"
    assert asyncio.run(registry.execute(WORKER, TOOL, {"extra": None})) == "ok"
    assert handler.call_count == 2


@pytest.mark.parametrize(
    ("worker", "registered", "error", "message"),
    [
        ("research_agent", True, PermissionError, "not visible"),
        ("research_agent", False, PermissionError, "not visible"),
        (WORKER, False, LookupError, "no configured handler"),
        (WORKER, True, RuntimeError, "budget exhausted"),
    ],
)
def test_execution_guards_keep_precedence(worker, registered, error, message):
    registry = ToolRegistry(max_calls=0)
    handler = Mock()
    if registered:
        registry.register(TOOL, handler)

    with pytest.raises(error, match=message):
        asyncio.run(registry.execute(worker, TOOL, None))

    handler.assert_not_called()
    assert registry.calls_for(worker) == 0


def test_capability_filter_still_denies_before_validation():
    registry = ToolRegistry(capability_filter={WORKER: set()})
    handler = Mock()
    registry.register(TOOL, handler)

    with pytest.raises(PermissionError):
        asyncio.run(registry.execute(WORKER, TOOL, None))

    handler.assert_not_called()
    assert registry.calls_for(WORKER) == 0


def test_invalid_attempt_exhausts_only_its_worker_budget():
    registry = ToolRegistry(max_calls=1)
    handler = Mock(return_value="ok")
    registry.register(TOOL, handler)

    with pytest.raises(TypeError):
        asyncio.run(registry.execute(WORKER, TOOL, {}))
    with pytest.raises(RuntimeError, match="budget exhausted"):
        asyncio.run(registry.execute(WORKER, TOOL, {"symptoms": "valid"}))
    handler.assert_not_called()
    assert asyncio.run(
        registry.execute("consultation_agent", TOOL, {"symptoms": "valid"})
    ) == "ok"
    handler.assert_called_once_with(symptoms="valid")
    assert registry.calls_for(WORKER) == 1
    assert registry.calls_for("consultation_agent") == 1


def test_handler_errors_still_consume_budget():
    registry = ToolRegistry()
    handler = Mock(side_effect=TypeError("handler failure"))
    registry.register(TOOL, handler)

    with pytest.raises(TypeError, match="handler failure"):
        asyncio.run(registry.execute(WORKER, TOOL, {"symptoms": "valid"}))

    handler.assert_called_once_with(symptoms="valid")
    assert registry.calls_for(WORKER) == 1


@pytest.mark.parametrize(
    ("arguments", "message"),
    INVALID_ARGUMENTS + [({"symptoms": ""}, "minLength"), ({"symptoms": "x" * 9}, "maxLength")],
)
def test_agent_loop_traces_validation_errors_without_extra_generation(
    tmp_path, bounded_schema, arguments, message
):
    registry = ToolRegistry()
    handler = Mock()
    registry.register(TOOL, handler)
    question = "Assess safely"
    request_spec = build_request_spec(question)
    request_ids = request_spec.required_item_ids
    contract = build_answer_contract(question)
    complexity = build_complexity_profile(question, contract)
    llm = ScriptedLLM(
        [
            LLMResponse(tool_calls=[ToolCall("invalid-call", TOOL, arguments)]),
            {"answers": [{"request_item_id": item, "answer": "Draft"} for item in request_ids]},
        ]
    )
    trace = TraceRecorder(tmp_path)

    result = asyncio.run(
        AgentLoop(llm, registry).execute(
            DIAGNOSTIC_AGENT,
            Subtask("t1", question, WORKER, request_item_ids=request_ids),
            request_spec,
            contract,
            EvidenceLedger(),
            {"question": question},
            [],
            load_public_skills()[WORKER],
            trace,
            complexity,
            build_response_profile(complexity),
        )
    )

    handler.assert_not_called()
    assert result.success
    assert result.tool_calls == registry.calls_for(WORKER) == 1
    assert len(llm.requests) == 2
    assert not llm.responses
    assert llm.requests[0]["tools"] == [bounded_schema]
    events = read_trace(trace.run_dir)
    call = next(event for event in events if event["event_type"] == "tool_call")
    tool_results = [event for event in events if event["event_type"] == "tool_result"]
    assert len(tool_results) == 1
    tool_result = tool_results[0]
    assert tool_result["parent_event_id"] == call["event_id"]
    output = tool_result["payload"]["result"]
    assert message in output["error"]
    assert "private-value" not in output["error"]
    assert "private-key" not in output["error"]
    tool_message = next(msg for msg in llm.requests[1]["messages"] if msg["role"] == "tool")
    assert tool_message["tool_call_id"] == "invalid-call"
    assert json.loads(tool_message["content"]) == output
