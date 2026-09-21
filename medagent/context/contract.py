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
    (r"test|workup|检查|确诊", "FURTHER_TESTS"),
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
