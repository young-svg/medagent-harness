from __future__ import annotations

import asyncio
import json

import pytest

from medagent.context.request_spec import RequestItem, RequestSpec
from medagent.llm.fakes import ScriptedLLM
from medagent.presentation.transform import (
    TaggedTextParseError,
    parse_tagged_presentation,
    transform_presentation,
)
from medagent.runtime.config import RuntimeConfig
from medagent.runtime.native_engine import NativeMedAgentEngine


def tagged_output(
    title: str = "最可能诊断",
    item: str = "最可能是胃食管反流病。",
    explanation: str = "饭后反酸符合反流表现。",
) -> str:
    return (
        f"<DIRECT_TITLE>\n{title}\n\n<DIRECT_ITEMS>\n- {item}\n\n"
        f"<PLAIN_EXPLANATION>\n- {explanation}\n<END_PRESENTATION>"
    )


def required_spec(count: int = 1) -> RequestSpec:
    labels = ["最可能诊断", "鉴别诊断", "进一步检查", "治疗与随访"]
    return RequestSpec([
        RequestItem(f"RQ{index + 1}", label, True, index + 1, label)
        for index, label in enumerate(labels[:count])
    ])


def sectioned_output(ids: list[str]) -> str:
    sections = "\n".join(
        f'<DIRECT_SECTION id="{item_id}" title="{item_id}的答案">\n'
        f"- {item_id}的具体结论。\n</DIRECT_SECTION>"
        for item_id in ids
    )
    return (
        "<DIRECT_TITLE>\n结论与下一步\n<DIRECT_ITEMS>\n"
        f"{sections}\n<PLAIN_EXPLANATION>\n- 因为需要逐项回答。\n<END_PRESENTATION>"
    )


def test_single_required_request_item_has_direct_coverage() -> None:
    result = parse_tagged_presentation(sectioned_output(["RQ1"]), required_spec())
    assert [section.request_item_id for section in result.direct_answer_sections or []] == [
        "RQ1"
    ]


def test_four_required_request_items_have_direct_coverage() -> None:
    result = parse_tagged_presentation(
        sectioned_output(["RQ1", "RQ2", "RQ3", "RQ4"]), required_spec(4)
    )
    assert len(result.direct_answer_sections or []) == 4
    assert len(result.direct_answer_items) == 4


def test_missing_required_direct_sections_rejects_for_fallback() -> None:
    with pytest.raises(TaggedTextParseError, match="RQ2,RQ4"):
        parse_tagged_presentation(sectioned_output(["RQ1", "RQ3"]), required_spec(4))


def test_duplicate_required_direct_section_rejected() -> None:
    with pytest.raises(TaggedTextParseError, match="duplicate_request_item_id"):
        parse_tagged_presentation(sectioned_output(["RQ1", "RQ1"]), required_spec())


def test_placeholder_direct_section_rejected() -> None:
    content = sectioned_output(["RQ1"]).replace("- RQ1的具体结论。", "- 参见下文。")
    with pytest.raises(TaggedTextParseError, match="placeholder_direct_section"):
        parse_tagged_presentation(content, required_spec())


@pytest.mark.asyncio
async def test_transform_sends_only_required_items_and_preserves_final_answer() -> None:
    spec = required_spec(4)
    spec = RequestSpec([*spec.items, RequestItem("RQ5", "可选信息", False, 5, "可选信息")])
    final_answer = "完整专业答案\n第二行原文。"
    llm = ScriptedLLM([sectioned_output(["RQ1", "RQ2", "RQ3", "RQ4"])])
    result, _ = await transform_presentation(llm, "病例", "问题", final_answer, spec)
    payload = json.loads(llm.requests[0]["messages"][1]["content"])
    assert [item["id"] for item in payload["required_request_items"]] == [
        "RQ1", "RQ2", "RQ3", "RQ4"
    ]
    assert payload["final_professional_answer"] == final_answer
    assert "trace" not in payload
    assert result.direct_answer_items == [f"RQ{i}的具体结论。" for i in range(1, 5)]
    assert llm.requests[0]["max_tokens"] == 6144


def test_standard_tagged_output_parses() -> None:
    result = parse_tagged_presentation(tagged_output())
    assert result.direct_answer_title == "最可能诊断"
    assert result.direct_answer_items == ["最可能是胃食管反流病。"]
    assert result.plain_explanation == ["饭后反酸符合反流表现。"]


def test_crlf_and_whitespace_around_tags_parse() -> None:
    content = tagged_output().replace("<DIRECT_TITLE>", "  <DIRECT_TITLE>  ")
    result = parse_tagged_presentation(content.replace("\n", "\r\n"))
    assert result.direct_answer_items == ["最可能是胃食管反流病。"]


def test_mixed_bullet_symbols_parse() -> None:
    content = """<DIRECT_TITLE>
现在怎么做
<DIRECT_ITEMS>
- 尽早下床活动。
* 观察伤口出血。
• 与医生确定药物方案。
<PLAIN_EXPLANATION>
• 活动有助于血液循环。
* 药物可能增加出血风险。
<END_PRESENTATION>"""
    result = parse_tagged_presentation(content)
    assert len(result.direct_answer_items) == 3
    assert len(result.plain_explanation) == 2


def test_full_markdown_fence_parses() -> None:
    result = parse_tagged_presentation(f"```text\n{tagged_output()}\n```")
    assert result.direct_answer_title == "最可能诊断"


def test_matching_optional_closing_tags_parse() -> None:
    content = sectioned_output(["RQ1"])
    content = content.replace("<PLAIN_EXPLANATION>", "</DIRECT_ITEMS>\n<PLAIN_EXPLANATION>")
    content = content.replace("<END_PRESENTATION>", "</PLAIN_EXPLANATION>\n<END_PRESENTATION>")
    assert parse_tagged_presentation(content, required_spec()).direct_answer_items == [
        "RQ1的具体结论。"
    ]


def test_missing_end_requires_clear_blank_section_close() -> None:
    content = tagged_output().replace("<END_PRESENTATION>", "\n")
    result = parse_tagged_presentation(content)
    assert result.plain_explanation == ["饭后反酸符合反流表现。"]


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (
            "<DIRECT_TITLE>\n诊断\n<PLAIN_EXPLANATION>\n- 原因\n<END_PRESENTATION>",
            "missing_or_out_of_order_section",
        ),
        (
            "<DIRECT_TITLE>\n诊断\n<DIRECT_ITEMS>\n- 结论\n"
            "<PLAIN_EXPLANATION>\n<END_PRESENTATION>",
            "empty_section:PLAIN_EXPLANATION",
        ),
        (
            "<DIRECT_TITLE>\n诊断\n<DIRECT_ITEMS>\n- 结论\n"
            "<PLAIN_EXPLANATION>\n- 原因",
            "truncated_output",
        ),
        (tagged_output() + "\n额外说明", "unexpected_text"),
        (
            tagged_output().replace(
                "<END_PRESENTATION>", "额外无法识别的正文\n<END_PRESENTATION>"
            ),
            "invalid_bullet:PLAIN_EXPLANATION",
        ),
        (
            "前言\n" + tagged_output(),
            "unexpected_text",
        ),
    ],
)
def test_invalid_tagged_output_is_rejected(content: str, reason: str) -> None:
    with pytest.raises(TaggedTextParseError) as error:
        parse_tagged_presentation(content)
    assert error.value.reason == reason


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("question", "title", "item", "why"),
    [
        ("这个病例现在应该怎么处理？", "现在怎么做", "尽早下床活动。", "活动有助于血液循环。"),
        ("最可能诊断是什么？", "最可能诊断", "最可能是胃食管反流病。", "饭后反酸符合反流表现。"),
        ("还需要完善哪些检查？", "下一步检查", "安排上消化道内镜。", "吞咽困难需要查看食管。"),
    ],
)
async def test_one_call_uses_only_post_answer_inputs(
    question: str, title: str, item: str, why: str
) -> None:
    final_answer = "已完成的专业医学答案。"
    llm = ScriptedLLM([tagged_output(title, item, why)])
    transformed, _ = await transform_presentation(llm, "原始病例", question, final_answer)

    assert transformed.direct_answer_title == title
    assert transformed.direct_answer_items == [item]
    assert transformed.plain_explanation == [why]
    assert len(llm.requests) == 1
    assert llm.requests[0]["response_format"] is None
    assert llm.requests[0]["max_tokens"] == 6144
    payload = json.loads(llm.requests[0]["messages"][1]["content"])
    assert payload == {
        "case_description": "原始病例",
        "user_question": question,
        "final_professional_answer": final_answer,
    }
    system_prompt = llm.requests[0]["messages"][0]["content"]
    assert "禁止新增" in system_prompt
    assert "不要 JSON" in system_prompt


class TimeoutPresentationLLM(ScriptedLLM):
    async def complete(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise TimeoutError("presentation timeout")


def run_engine(tmp_path, presentation_llm: ScriptedLLM) -> dict:  # type: ignore[type-arg]
    worker_llm = ScriptedLLM(
        [
            {
                "subtasks": [
                    {
                        "subtask_id": "ST1",
                        "description": "Assess",
                        "assigned_agent": "diagnostic_agent",
                    }
                ]
            },
            "诊断：胃食管反流病。\n请结合症状复评。",
        ]
    )
    engine = NativeMedAgentEngine(
        RuntimeConfig(trace_dir=str(tmp_path)),
        llm=worker_llm,
        presentation_llm=presentation_llm,
    )

    async def run():  # type: ignore[no-untyped-def]
        result = await engine.analyze("45岁男性反酸", "最可能诊断是什么？", "v3-test")
        await engine.close()
        return result

    return asyncio.run(run())


@pytest.mark.parametrize(
    "presentation_llm",
    [ScriptedLLM(["<DIRECT_TITLE>\n诊断"]), TimeoutPresentationLLM([])],
)
def test_presentation_failure_preserves_completed_professional_answer(
    tmp_path, presentation_llm: ScriptedLLM
) -> None:
    result = run_engine(tmp_path, presentation_llm)
    presentation = result["presentation"]
    assert result["status"] == "completed"
    assert presentation["professional_answer"] == result["final_answer"]
    assert presentation["direct_answer_items"] == ["胃食管反流病。"]
    assert presentation["plain_explanation"] == []
    events = presentation["execution_summary"]["trace_events"]
    transform = next(item for item in events if item["event_type"] == "presentation_transform")
    assert transform["payload"]["status"] == "fallback"
    assert transform["payload"]["protocol"] == "tagged_text"
    assert transform["payload"]["failure_reason"]
    assert transform["payload"]["parse_status"] in {"invalid", "provider_error"}


def test_successful_transform_preserves_final_answer_and_trace(tmp_path) -> None:
    presentation_llm = ScriptedLLM([sectioned_output(["RQ1"])])
    result = run_engine(tmp_path, presentation_llm)
    presentation = result["presentation"]
    assert result["status"] == "completed"
    assert presentation["professional_answer"] == result["final_answer"]
    assert presentation["direct_answer_title"] == "结论与下一步"
    assert presentation["direct_answer_items"] == ["RQ1的具体结论。"]
    assert presentation["plain_explanation"] == ["因为需要逐项回答。"]
    assert presentation["direct_answer_sections"][0]["request_item_id"] == "RQ1"
    assert result["trace"]["llm_calls"] == 3
    events = presentation["execution_summary"]["trace_events"]
    transform = next(item for item in events if item["event_type"] == "presentation_transform")
    assert transform["payload"]["status"] == "success"
    assert transform["payload"]["parse_status"] == "valid"
    assert transform["payload"]["protocol"] == "tagged_text"
    assert transform["payload"]["direct_covered_request_item_ids"] == ["RQ1"]
    assert len(presentation_llm.requests) == 1
