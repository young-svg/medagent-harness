from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from medagent.context.models import CoverageItem


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
        if self.breadth not in {"focused", "balanced", "broad"}:
            raise ValueError("invalid breadth")
        for item in self.coverage_checklist:
            item.validate()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["requested_deliverable"] = self.requested_deliverables[0]
        return data

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


def build_answer_contract(question: str) -> AnswerContract:
    text = question.lower()
    checks: list[str]
    if any(term in text for term in ("检查", "test", "workup", "确诊")):
        intent, deliverable, mode, breadth = (
            "diagnostic_workup",
            "FURTHER_TESTS",
            "grouped_workup",
            "broad",
        )
        checks = [
            "tests confirming the leading diagnosis",
            "tests excluding major alternatives",
            "severity or complication assessment when relevant",
            "decision-changing tests",
        ]
    elif any(term in text for term in ("治疗", "management", "treatment")):
        intent, deliverable, mode, breadth = (
            "management",
            "TREATMENT_PLAN",
            "prioritized_management",
            "focused",
        )
        checks = ["immediate priority", "standard treatment", "decision prerequisites"]
    elif any(term in text for term in ("诊断", "diagnos", "鉴别")):
        intent, deliverable, mode, breadth = (
            "diagnosis",
            "MOST_LIKELY_DIAGNOSIS",
            "diagnostic_assessment",
            "balanced",
        )
        checks = ["most likely diagnosis", "decisive supporting findings", "major differentials"]
    else:
        intent, deliverable, mode, breadth = (
            "comprehensive_case_analysis",
            "COMPREHENSIVE_CASE_ANALYSIS",
            "case_analysis",
            "balanced",
        )
        checks = [
            "problem representation",
            "leading diagnosis and decisive evidence",
            "important differential diagnoses",
            "next decision-changing step",
        ]
    return AnswerContract(
        intent=intent,
        requested_deliverables=[deliverable],
        response_mode=mode,
        must_cover=checks,
        avoid=["unsupported patient facts", "unrequested encyclopedic detail"],
        breadth=breadth,
        coverage_checklist=[CoverageItem(item=item) for item in checks],
    )
