from __future__ import annotations

import os
import re
from typing import Any

from medagent.context.contract import AnswerContract
from medagent.context.evidence_ledger import EvidenceLedger
from medagent.presentation.models import (
    EvidenceCard,
    PresentationResponse,
)
from medagent.presentation.transform import PresentationTransform
from medagent.retrieval.evidence import EvidenceBundle

_DIRECT_LABEL = re.compile(
    r"^(?:最终诊断|最可能的?诊断|诊断考虑|诊断|核心判断|初步判断|考虑|推荐)"
    r"(?:\s*[（(][^）)]*[）)])?\s*[:：]\s*(.*)$",
    re.I,
)
_EXPLANATORY_OPENING = re.compile(
    r"^(?:免责声明|安全提示|注意事项|重要说明|说明|提示|以下(?:内容|分析)|"
    r"以下依据|本回答|本结果|仅供参考|依据[:：])",
    re.I,
)
_CORE_SECTION = re.compile(
    r"^(?:[一二三四五六七八九十]+[、.]\s*)?核心[^：:\n]{0,20}(?:结论|建议|判断|措施|管理)"
)


def _display_line(value: str) -> str:
    cleaned = (
        re.sub(r"^\s{0,3}#{1,6}\s*", "", value)
        .replace("**", "")
        .replace("__", "")
        .strip()
    )
    return re.sub(r"^[-+*]\s+", "", cleaned)


def _presentation_heading(value: str) -> bool:
    if re.match(r"^\s{0,3}#{1,6}\s+", value):
        return True
    cleaned = _display_line(value)
    if re.fullmatch(r"【[^】]{1,80}】", cleaned):
        return True
    normalized = re.sub(r"^(?:\d+|[一二三四五六七八九十]+)[.)、．：:]?\s*", "", cleaned)
    if re.match(r"^RQ\d+\s*[:：]", normalized, re.I):
        return True
    return bool(
        len(normalized) <= 80
        and re.search(
            r"(?:综合病例分析|病例分析|医学分析|临床分析|核心结论|结论|"
            r"最可能(?:的)?诊断(?:\s*[与及和]\s*(?:诊断)?依据)?|"
            r"诊断(?:\s*[与及和]\s*依据)?|鉴别诊断|检查建议|治疗方案|随访计划|参考依据|总体原则)"
            r"(?:\s*[（(][^）)]*[）)])?$",
            normalized,
            re.I,
        )
    )


def _next_substantive_line(lines: list[str], start: int) -> str | None:
    for raw in lines[start:]:
        candidate = _display_line(raw)
        if not candidate or _EXPLANATORY_OPENING.match(candidate):
            continue
        if _presentation_heading(raw) or _CORE_SECTION.match(candidate):
            continue
        return candidate
    return None


def extract_direct_answer(final_answer: str) -> str:
    """Select an explicit conclusion without treating disclaimers or headings as answers."""

    lines = final_answer.replace("\r\n", "\n").splitlines()
    for index, raw in enumerate(lines):
        candidate = _display_line(raw)
        match = _DIRECT_LABEL.match(candidate)
        if not match:
            continue
        inline = match.group(1).strip()
        if inline:
            return inline
        following = _next_substantive_line(lines, index + 1)
        if following:
            return following

    for index, raw in enumerate(lines):
        if not _CORE_SECTION.match(_display_line(raw)):
            continue
        following = _next_substantive_line(lines, index + 1)
        if following:
            return following

    for raw in lines:
        candidate = _display_line(raw)
        if not candidate or _EXPLANATORY_OPENING.match(candidate):
            continue
        if _presentation_heading(raw):
            continue
        return candidate

    paragraphs = [item.strip() for item in re.split(r"\n\s*\n", final_answer) if item.strip()]
    return paragraphs[0].splitlines()[0] if paragraphs else "Analysis unavailable"


def _physical_collection(logical: str | None) -> str | None:
    if not logical or os.getenv("MEDICAL_KB_ENABLED", "").strip().casefold() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return logical
    native_general = os.getenv("MEDAGENT_GENERIC_COLLECTION", "clinical_knowledge")
    native_special = os.getenv("MEDAGENT_SPECIAL_COLLECTION", "clinical_guidelines")
    if logical == native_general:
        return os.getenv("MEDICAL_KB_GENERAL_COLLECTION", "medical_knowledge_v1")
    if logical == native_special:
        return os.getenv("MEDICAL_KB_SPECIAL_COLLECTION", "medical_knowledge")
    return logical


def build_retrieval_summary(execution: dict[str, Any]) -> dict[str, Any] | None:
    events = execution.get("trace_events")
    if not isinstance(events, list):
        events = []
    queries = [
        event.get("payload", {})
        for event in events
        if isinstance(event, dict) and event.get("event_type") == "retrieval_query"
    ]
    results = [
        event.get("payload", {})
        for event in events
        if isinstance(event, dict) and event.get("event_type") == "retrieval_result"
    ]
    retrieval = execution.get("retrieval")
    retrieval = retrieval if isinstance(retrieval, dict) else {}
    if not queries and not results and not retrieval.get("query"):
        return None

    raw_candidates = [
        candidate
        for result in results
        for candidate in result.get("raw_candidates", [])
        if isinstance(candidate, dict)
    ]
    admissions = [
        admission
        for result in results
        for admission in result.get("admission", [])
        if isinstance(admission, dict)
    ]
    logical = next(
        (str(item.get("collection")) for item in queries if item.get("collection")),
        str(retrieval.get("collection") or "") or None,
    )
    query_values = list(
        dict.fromkeys(str(item.get("query")) for item in queries if item.get("query"))
    )
    if not query_values and retrieval.get("query"):
        query_values = [str(retrieval["query"])]
    scores = [
        float(item["score"])
        for item in raw_candidates
        if isinstance(item.get("score"), int | float)
    ]
    admitted_ids = retrieval.get("admitted_evidence_ids")
    admitted_ids = admitted_ids if isinstance(admitted_ids, list) else []
    return {
        "query": query_values[0] if query_values else None,
        "queries": query_values,
        "query_count": len(query_values),
        "logical_collection": logical,
        "physical_collection": _physical_collection(logical),
        "top_k": next((item.get("top_k") for item in queries if item.get("top_k")), None),
        "candidate_count": len(raw_candidates)
        if results
        else int(retrieval.get("retrieved_count", 0)),
        "admitted_count": sum(bool(item.get("admitted")) for item in admissions)
        if results
        else len(admitted_ids),
        "unique_evidence_count": len(admitted_ids),
        "top_score": max(scores) if scores else None,
    }


class PresentationAdapter:
    """Build three-view data without adding clinical claims."""

    def adapt(
        self,
        final_answer: str,
        contract: AnswerContract,
        ledger: EvidenceLedger,
        evidence: EvidenceBundle,
        trace_metadata: dict[str, Any],
        developer: dict[str, Any] | None = None,
        transformed: PresentationTransform | None = None,
    ) -> PresentationResponse:
        direct_answer = extract_direct_answer(final_answer)
        cards = [
            EvidenceCard(
                item.evidence_id,
                item.title,
                item.source or "Source metadata unavailable",
                item.section,
                item.score,
                item.text[:300],
            )
            for item in evidence.admitted_items
        ]
        execution = {
            **trace_metadata,
            "runtime_mode": "native",
            "contract": contract.to_dict(),
            "ledger": ledger.to_dict(),
            **(developer or {}),
        }
        retrieval_summary = build_retrieval_summary(execution)
        if retrieval_summary is not None:
            execution["retrieval_summary"] = retrieval_summary
        return PresentationResponse(
            headline=transformed.direct_answer_title if transformed else direct_answer,
            plain_language_summary="\n\n".join(transformed.plain_explanation)
            if transformed
            else "",
            professional_answer=final_answer,
            direct_answer="\n".join(transformed.direct_answer_items)
            if transformed
            else direct_answer,
            direct_answer_title=transformed.direct_answer_title if transformed else None,
            direct_answer_items=list(transformed.direct_answer_items)
            if transformed
            else [direct_answer],
            plain_explanation=list(transformed.plain_explanation) if transformed else [],
            evidence_cards=cards,
            execution_summary=execution,
        )
