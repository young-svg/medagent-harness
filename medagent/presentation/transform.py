"""One post-answer expression transform using a conservative tagged-text protocol."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from medagent.llm.client import LLMClient, LLMResponse

_SYSTEM_PROMPT = """你是医学答案的展示编辑，不是诊断 Agent。
只能根据已完成的专业答案重新组织表达，不重新医学推理，也不调用工具。
禁止新增专业答案中没有的诊断、药物、剂量、检查、指南、风险判断或病例事实。
不得声称排除了专业答案尚未排除的疾病。
DIRECT_TITLE 按用户问题写具体标题，例如“现在怎么做”“最可能诊断”“下一步检查”。
DIRECT_ITEMS 直接给结论或行动，优先 3–6 条，每条尽量一句话。
问诊断先给诊断，问检查先列检查，问治疗或管理先列具体做法。
不要以免责声明、空泛标题或长篇医学原理充当直接答案。
PLAIN_EXPLANATION 用普通人能理解的语言说明原因，建立简单因果关系，2–5条。
每一类关键行动至少解释一次为什么有帮助，尤其不要遗漏第一条具体行动。
若专业答案建议术后活动或机械预防，要解释这些措施为何有助于降低血栓风险。
不要重复行动清单，尽量不用缩写；必要术语顺手解释。
第三层专业答案由系统原样展示，你不能改写。
只输出以下标签和内容，不要 JSON、前言、附言或额外标签：
<DIRECT_TITLE>
实际标题
<DIRECT_ITEMS>
- 实际具体结论或行动
<PLAIN_EXPLANATION>
- 实际通俗原因
<END_PRESENTATION>
标签必须原样输出，条目以“- ”开头；示意文字不可复制。"""

_SECTIONS = ("<DIRECT_TITLE>", "<DIRECT_ITEMS>", "<PLAIN_EXPLANATION>")
_END = "<END_PRESENTATION>"
_BULLET = re.compile(r"^[-*•]\s*(\S.*)$")
_FENCE = re.compile(r"^\s*```[^\n]*\n(?P<body>[\s\S]*?)\n```\s*$")


@dataclass(frozen=True, slots=True)
class PresentationTransform:
    direct_answer_title: str
    direct_answer_items: list[str]
    plain_explanation: list[str]


class TaggedTextParseError(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class PresentationParseError(ValueError):
    """Invalid visible presentation output with provider diagnostics attached."""

    def __init__(self, response: LLMResponse, cause: TaggedTextParseError) -> None:
        super().__init__(f"presentation tagged text invalid: {cause.reason}")
        self.response = response
        self.cause_type = type(cause).__name__
        self.failure_reason = cause.reason


def _parse_bullets(lines: list[str], section: str) -> list[str]:
    items = []
    for line in lines:
        match = _BULLET.fullmatch(line)
        if not match:
            raise TaggedTextParseError(f"invalid_bullet:{section}")
        items.append(match.group(1).strip())
    if not items:
        raise TaggedTextParseError(f"empty_section:{section}")
    return items


def parse_tagged_presentation(content: str) -> PresentationTransform:
    """Accept only ordered complete sections, known bullets and a clear ending."""

    normalized = content.replace("\r\n", "\n")
    if normalized.lstrip().startswith("```"):
        fenced = _FENCE.fullmatch(normalized)
        if not fenced:
            raise TaggedTextParseError("invalid_fence")
        normalized = fenced.group("body")

    sections: dict[str, list[str]] = {tag: [] for tag in _SECTIONS}
    section_index = -1
    ended = False
    for raw_line in normalized.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if line in _SECTIONS:
            if ended or _SECTIONS.index(line) != section_index + 1:
                raise TaggedTextParseError("missing_or_out_of_order_section")
            section_index += 1
            continue
        if line == _END:
            if ended or section_index != len(_SECTIONS) - 1:
                raise TaggedTextParseError("missing_or_out_of_order_section")
            ended = True
            continue
        if section_index < 0 or ended:
            raise TaggedTextParseError("unexpected_text")
        sections[_SECTIONS[section_index]].append(line)

    if section_index != len(_SECTIONS) - 1:
        raise TaggedTextParseError("missing_core_section")
    if not ended and not re.search(r"\n\s*\n\s*$", normalized):
        raise TaggedTextParseError("truncated_output")

    title_lines = sections["<DIRECT_TITLE>"]
    if len(title_lines) != 1 or not title_lines[0].strip():
        raise TaggedTextParseError("invalid_title")
    return PresentationTransform(
        direct_answer_title=title_lines[0],
        direct_answer_items=_parse_bullets(sections["<DIRECT_ITEMS>"], "DIRECT_ITEMS"),
        plain_explanation=_parse_bullets(
            sections["<PLAIN_EXPLANATION>"], "PLAIN_EXPLANATION"
        ),
    )


async def transform_presentation(
    llm: LLMClient, description: str, question: str, final_answer: str
) -> tuple[PresentationTransform, LLMResponse]:
    response = await llm.complete(
        [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "case_description": description,
                        "user_question": question,
                        "final_professional_answer": final_answer,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        max_tokens=4096,
    )
    try:
        return parse_tagged_presentation(response.content), response
    except TaggedTextParseError as error:
        raise PresentationParseError(response, error) from error
