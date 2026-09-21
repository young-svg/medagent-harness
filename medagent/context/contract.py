from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from medagent.context.models import CoverageItem

_DIAGNOSTIC_OUTPUTS = "MOST_LIKELY_DIAGNOSIS DIAGNOSIS_WITH_BASIS DIAGNOSTIC_BASIS"
_DIAGNOSTIC_OUTPUTS += " DIFFERENTIAL_DIAGNOSIS DIAGNOSIS_LIST HISTORY_CLUES FURTHER_TESTS"
_CARE_OUTPUTS = "TREATMENT_PRINCIPLES TREATMENT_PLAN BEST_TREATMENT_PLAN SURGERY_INDICATION"
_CARE_OUTPUTS += " POSTOPERATIVE_MANAGEMENT POSTOPERATIVE_COMPLICATIONS PREOPERATIVE_EVALUATION"
_GENERAL_OUTPUTS = "PREVENTION COMPREHENSIVE_CASE_ANALYSIS CASE_ANALYSIS MULTI_DELIVERABLE"
DELIVERABLES = set(f"{_DIAGNOSTIC_OUTPUTS} {_CARE_OUTPUTS} {_GENERAL_OUTPUTS}".split())

_FURTHER_TESTS_HINT = (
    r"\b(?:further|additional|next(?:-|\s+)step)\s+(?:diagnostic\s+)?(?:tests?|testing|workup)\b"
    r"|\b(?:what|which)\s+(?:further\s+|additional\s+|other\s+)?(?:tests?|workup)\b"
    r"|\b(?:tests?|testing|workup)\s+(?:are\s+)?(?:needed|required|recommended)\b"
    r"|\b(?:diagnosis|differential)\s*,\s*(?:further\s+|additional\s+)?tests?\b"
    r"|\b(?:provide|list|recommend|suggest|identify|outline)\s+"
    r"(?:(?:the|any|necessary|recommended|appropriate|further|additional|diagnostic)\s+){0,3}"
    r"(?:tests?|testing|workup)\b"
    r"|(?:还|仍)需(?:要)?(?:进一步)?(?:做|进行|完善|补充|安排)?"
    r"(?:哪些|什么)?(?:辅助)?检查"
    r"|下一步(?:还)?(?:需(?:要)?|应该|应当|建议)?(?:进一步)?"
    r"(?:做|进行|完善|补充|安排)?(?:哪些|什么)?(?:辅助)?检查"
    r"|进一步(?:做|进行|完善|补充|安排)?(?:哪些|什么)?(?:辅助)?检查"
    r"|(?:需要|应该|应当)[^，。；;,.？！?\n]{0,12}(?:哪些|什么)(?:辅助)?检查"
    r"|建议[^，。；;,.？！?\n]{0,12}(?:进一步|补充|完善|做|进行)[^，。；;,.？！?\n]{0,8}检查"
)


@dataclass(slots=True)
class AnswerContract:
    intent: str
    requested_deliverables: list[str]
    response_mode: str = "case_analysis"
    must_cover: list[str] = field(default_factory=list)
    optional_cover: list[str] = field(default_factory=list)
    avoid: list[str] = field(default_factory=list)
    breadth: str = "balanced"
    coverage_checklist: list[CoverageItem] = field(default_factory=list)

    def validate(self) -> None:
        if not self.intent or not self.requested_deliverables:
            raise ValueError("intent and at least one requested deliverable are required")
        if any(item not in DELIVERABLES for item in self.requested_deliverables):
            raise ValueError("unknown requested deliverable")
        if self.breadth not in {"focused", "balanced", "broad"}:
            raise ValueError("invalid breadth")
        for item in self.coverage_checklist:
            item.validate()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> AnswerContract:
        data = dict(value)
        data.pop("requested_deliverable", None)
        data["coverage_checklist"] = [
            item if isinstance(item, CoverageItem) else CoverageItem.from_dict(item)
            for item in data.get("coverage_checklist", [])
        ]
        contract = cls(**data)
        contract.validate()
        return contract


_DELIVERABLE_HINTS = (
    (r"post.?operative complications?|术后并发", "POSTOPERATIVE_COMPLICATIONS"),
    (r"post.?operative|术后管理", "POSTOPERATIVE_MANAGEMENT"),
    (r"pre.?operative|术前", "PREOPERATIVE_EVALUATION"),
    (r"surgery indication|手术指征", "SURGERY_INDICATION"),
    (r"prevent|预防", "PREVENTION"),
    (r"treatment|management|治疗|处理", "TREATMENT_PLAN"),
    (_FURTHER_TESTS_HINT, "FURTHER_TESTS"),
    (r"differential|鉴别", "DIFFERENTIAL_DIAGNOSIS"),
    (r"diagnos|诊断", "DIAGNOSIS_WITH_BASIS"),
)


def _remove_explicitly_excluded_scope(question: str) -> str:
    patterns = (
        r"\b(?:do not|don't|without)\s+(?:provide|make|perform|include)?\s*"
        r"(?:a\s+)?(?:patient[- ]specific\s+)?diagnos\w*",
        r"不(?:作|做|进行|需|需要|要求|要)?[^，。；;,.]{0,12}诊断",
    )
    scoped = question
    for pattern in patterns:
        scoped = re.sub(pattern, "", scoped, flags=re.I)
    return scoped


def build_answer_contract(question: str) -> AnswerContract:
    requested_scope = _remove_explicitly_excluded_scope(question)
    matches = [
        value
        for pattern, value in _DELIVERABLE_HINTS
        if re.search(pattern, requested_scope, re.I)
    ]
    deliverables = list(dict.fromkeys(matches)) or ["COMPREHENSIVE_CASE_ANALYSIS"]
    intent = "multi_deliverable" if len(deliverables) > 1 else deliverables[0].lower()
    must_cover = [item.replace("_", " ").lower() for item in deliverables]
    contract = AnswerContract(
        intent=intent,
        requested_deliverables=deliverables,
        response_mode="structured_clinical_answer",
        must_cover=must_cover,
        avoid=["unsupported patient facts", "claims that recommended care was completed"],
        breadth="broad" if len(deliverables) > 1 else "balanced",
        coverage_checklist=[CoverageItem(item=item) for item in must_cover],
    )
    contract.validate()
    return contract
