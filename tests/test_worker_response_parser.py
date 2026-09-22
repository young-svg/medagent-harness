from __future__ import annotations

import json

from medagent.runtime.agent_loop import parse_worker_response


def response_json(*pairs: tuple[str, str]) -> str:
    return json.dumps(
        {
            "answers": [
                {"request_item_id": request_item_id, "answer": answer}
                for request_item_id, answer in pairs
            ]
        },
        ensure_ascii=False,
    )


def test_clean_valid_json_is_direct() -> None:
    result = parse_worker_response(response_json(("RQ1", "one")), ["RQ1"])

    assert result.parse_status == "direct_json"
    assert result.recovery_method is None
    assert [item.to_dict() for item in result.answers] == [
        {"request_item_id": "RQ1", "answer": "one"}
    ]


def test_valid_json_plus_med058_trailing_closers_is_recovered() -> None:
    content = response_json(("RQ1", "one"), ("RQ2", "two")) + "]}"

    result = parse_worker_response(content, ["RQ1", "RQ2"])

    assert result.parse_status == "recovered_json"
    assert result.recovery_method == "trailing_closing_delimiters"
    assert [item.request_item_id for item in result.answers] == ["RQ1", "RQ2"]
    assert [item.answer for item in result.answers] == ["one", "two"]


def test_valid_json_plus_two_closing_braces_is_recovered() -> None:
    result = parse_worker_response(
        response_json(("RQ1", "one")) + "}}",
        ["RQ1"],
    )

    assert result.parse_status == "recovered_json"
    assert result.recovery_method == "trailing_closing_delimiters"
    assert [item.request_item_id for item in result.answers] == ["RQ1"]


def test_valid_json_plus_second_json_object_is_rejected() -> None:
    content = response_json(("RQ1", "one")) + response_json(("RQ1", "two"))

    result = parse_worker_response(content, ["RQ1"])

    assert result.parse_status == "invalid"
    assert result.recovery_method is None
    assert result.answers == []


def test_valid_json_plus_explanatory_text_is_rejected() -> None:
    result = parse_worker_response(
        response_json(("RQ1", "one")) + " This is an explanation.",
        ["RQ1"],
    )

    assert result.parse_status == "invalid"
    assert result.recovery_method is None
    assert result.answers == []


def test_truncated_json_is_rejected() -> None:
    content = response_json(("RQ1", "one"))[:-1]

    result = parse_worker_response(content, ["RQ1"])

    assert result.parse_status == "invalid"
    assert result.recovery_method is None
    assert result.answers == []


def test_schema_invalid_json_plus_redundant_closers_is_rejected() -> None:
    content = json.dumps(
        {"answers": [{"request_item_id": "RQ9", "answer": "not assigned"}]}
    ) + "]}"

    result = parse_worker_response(content, ["RQ1", "RQ2"])

    assert result.parse_status == "invalid"
    assert result.recovery_method is None
    assert result.answers == []
