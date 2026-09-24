"""One post-answer expression transform using a conservative tagged-text protocol."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from medagent.context.request_spec import RequestSpec
from medagent.llm.client import LLMClient, LLMResponse

_SYSTEM_PROMPT = """你是医学答案的展示编辑，不是诊断 Agent。
只能根据已完成的专业答案重新组织表达，不重新医学推理，也不调用工具。
禁止新增专业答案中没有的诊断、药物、剂量、检查、指南、风险判断或病例事实。
不得声称排除了专业答案尚未排除的疾病。
DIRECT_TITLE 按用户问题写具体标题，例如“现在怎么做”“最可能诊断”“下一步检查”。
DIRECT_ITEMS 直接给结论或行动；复合请求可按必答项分组，不限制总条数。
必须保留用户请求覆盖：每个 required request item 都要在 DIRECT_ITEMS 中恰好对应一个
<DIRECT_SECTION id="RQ编号" title="该项具体主题">，并以 </DIRECT_SECTION> 结束。
每个 DIRECT_SECTION 至少有一条具体答案，不得只写标题、待定或参见下文。
不得遗漏用户明确要求的诊断、鉴别诊断、检查、治疗、随访或其他交付项。
直接层可以简洁，但完整覆盖优先于任意字数限制；不能只选你认为最重要的行动。
每个必答项只保留核心结论，优先 1–3 条；每条只写一句，不复述完整专业答案。
若一个必答项同时要求多个交付物，应以带主题的条目分别回答它们。
问诊断先给诊断，问检查先列检查，问治疗或管理先列具体做法。
不要以免责声明、空泛标题或长篇医学原理充当直接答案。
PLAIN_EXPLANATION 总计 2–4 个短点，用普通人能理解的语言说明原因。
每一类关键行动至少解释一次为什么有帮助，尤其不要遗漏第一条具体行动。
若专业答案建议术后活动或机械预防，要解释这些措施为何有助于降低血栓风险。
不要重复行动清单，不写长篇专业分析；尽量不用缩写，必要术语顺手解释。
第三层专业答案由系统原样展示，你不能改写。
只输出以下标签和内容，不要 JSON、前言、附言或额外标签：
<DIRECT_TITLE>
实际标题
<DIRECT_ITEMS>
<DIRECT_SECTION id="RQ1" title="对应的必答主题">
- 对这个必答项的具体结论或行动
</DIRECT_SECTION>
<PLAIN_EXPLANATION>
- 实际通俗原因
<END_PRESENTATION>
标签必须原样输出，RQ编号必须与输入中的 required_request_items 对应。
只使用示例中的结束标签，不另加 </DIRECT_ITEMS> 或 </PLAIN_EXPLANATION>。
条目以“- ”开头；示意文字不可复制。"""

_SECTIONS = ("<DIRECT_TITLE>", "<DIRECT_ITEMS>", "<PLAIN_EXPLANATION>")
_SECTION_CLOSERS = ("</DIRECT_TITLE>", "</DIRECT_ITEMS>", "</PLAIN_EXPLANATION>")
_END = "<END_PRESENTATION>"
_BULLET = re.compile(r"^[-*•]\s*(\S.*)$")
_PLACEHOLDER = re.compile(r"^(?:待定|待检查|参见下文|详见下文|见下文|无|N/?A|TBD)[。.!！]?$", re.I)
_FENCE = re.compile(r"^\s*```[^\n]*\n(?P<body>[\s\S]*?)\n```\s*$")
_DIRECT_SECTION_START = re.compile(
    r'^<DIRECT_SECTION id="(?P<id>RQ\d+)" title="(?P<title>[^"<>]+)">$'
)
_DIRECT_SECTION_END = "</DIRECT_SECTION>"


@dataclass(frozen=True, slots=True)
class DirectSection:
    request_item_id: str
    title: str
    items: list[str]


@dataclass(frozen=True, slots=True)
class PresentationTransform:
    direct_answer_title: str
    direct_answer_items: list[str]
    plain_explanation: list[str]
    direct_answer_sections: list[DirectSection] | None = None


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


def _parse_direct_sections(lines: list[str]) -> list[DirectSection] | None:
    if not any(line.startswith("<DIRECT_SECTION") for line in lines):
        return None
    result: list[DirectSection] = []
    index = 0
    while index < len(lines):
        match = _DIRECT_SECTION_START.fullmatch(lines[index])
        if not match:
            raise TaggedTextParseError("invalid_direct_section")
        index += 1
        bullets: list[str] = []
        while index < len(lines) and lines[index] != _DIRECT_SECTION_END:
            bullets.append(lines[index])
            index += 1
        if index >= len(lines):
            raise TaggedTextParseError("unclosed_direct_section")
        parsed_bullets = _parse_bullets(bullets, "DIRECT_SECTION")
        if all(_PLACEHOLDER.fullmatch(item) for item in parsed_bullets):
            raise TaggedTextParseError("placeholder_direct_section")
        result.append(
            DirectSection(
                request_item_id=match.group("id"),
                title=match.group("title").strip(),
                items=parsed_bullets,
            )
        )
        index += 1
    return result


def _validate_coverage(sections: list[DirectSection] | None, spec: RequestSpec) -> None:
    required = spec.required_item_ids
    if not required:
        return
    if sections is None:
        raise TaggedTextParseError("missing_direct_sections")
    ids = [section.request_item_id for section in sections]
    if len(ids) != len(set(ids)):
        raise TaggedTextParseError("duplicate_request_item_id")
    missing = [item_id for item_id in required if item_id not in ids]
    extra = [item_id for item_id in ids if item_id not in required]
    if missing or extra:
        raise TaggedTextParseError(
            f"request_coverage_mismatch:missing={','.join(missing)};extra={','.join(extra)}"
        )


def parse_tagged_presentation(
    content: str, request_spec: RequestSpec | None = None
) -> PresentationTransform:
    """Accept only ordered complete sections, known bullets and a clear ending."""

    normalized = content.replace("\r\n", "\n")
    if normalized.lstrip().startswith("```"):
        fenced = _FENCE.fullmatch(normalized)
        if not fenced:
            raise TaggedTextParseError("invalid_fence")
        normalized = fenced.group("body")

    sections: dict[str, list[str]] = {tag: [] for tag in _SECTIONS}
    section_index = -1
    section_closed = False
    ended = False
    for raw_line in normalized.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if line in _SECTIONS:
            if ended or _SECTIONS.index(line) != section_index + 1:
                raise TaggedTextParseError("missing_or_out_of_order_section")
            section_index += 1
            section_closed = False
            continue
        if line in _SECTION_CLOSERS:
            if (
                ended
                or section_index < 0
                or line != _SECTION_CLOSERS[section_index]
                or section_closed
                or not sections[_SECTIONS[section_index]]
            ):
                raise TaggedTextParseError("invalid_section_closer")
            section_closed = True
            continue
        if line == _END:
            if ended or section_index != len(_SECTIONS) - 1:
                raise TaggedTextParseError("missing_or_out_of_order_section")
            ended = True
            continue
        if section_index < 0 or ended or section_closed:
            raise TaggedTextParseError("unexpected_text")
        sections[_SECTIONS[section_index]].append(line)

    if section_index != len(_SECTIONS) - 1:
        raise TaggedTextParseError("missing_core_section")
    if not ended and not re.search(r"\n\s*\n\s*$", normalized):
        raise TaggedTextParseError("truncated_output")

    title_lines = sections["<DIRECT_TITLE>"]
    if len(title_lines) != 1 or not title_lines[0].strip():
        raise TaggedTextParseError("invalid_title")
    direct_sections = _parse_direct_sections(sections["<DIRECT_ITEMS>"])
    if request_spec is not None:
        _validate_coverage(direct_sections, request_spec)
    direct_items = (
        [item for section in direct_sections for item in section.items]
        if direct_sections is not None
        else _parse_bullets(sections["<DIRECT_ITEMS>"], "DIRECT_ITEMS")
    )
    return PresentationTransform(
        direct_answer_title=title_lines[0],
        direct_answer_items=direct_items,
        plain_explanation=_parse_bullets(
            sections["<PLAIN_EXPLANATION>"], "PLAIN_EXPLANATION"
        ),
        direct_answer_sections=direct_sections,
    )


async def transform_presentation(
    llm: LLMClient,
    description: str,
    question: str,
    final_answer: str,
    request_spec: RequestSpec | None = None,
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
                        **({
                            "required_request_items": [
                                {
                                    "id": item.id,
                                    "text": item.text,
                                    "semantic_type": item.semantic_type,
                                }
                                for item in request_spec.items
                                if item.required
                            ]
                        } if request_spec is not None else {}),
                        "final_professional_answer": final_answer,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        max_tokens=6144,
    )
    try:
        return parse_tagged_presentation(response.content, request_spec), response
    except TaggedTextParseError as error:
        raise PresentationParseError(response, error) from error
