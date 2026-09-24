from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from medagent.context.contract import AnswerContract

_DIAGNOSIS = {
    "MOST_LIKELY_DIAGNOSIS",
    "DIAGNOSIS_WITH_BASIS",
    "DIAGNOSTIC_BASIS",
    "DIAGNOSIS_LIST",
    "HISTORY_CLUES",
}
_DIFFERENTIAL = {"DIFFERENTIAL_DIAGNOSIS"}
_TESTS = {"FURTHER_TESTS", "PREOPERATIVE_EVALUATION"}
_MANAGEMENT = {
    "TREATMENT_PRINCIPLES",
    "TREATMENT_PLAN",
    "BEST_TREATMENT_PLAN",
    "SURGERY_INDICATION",
    "POSTOPERATIVE_MANAGEMENT",
    "POSTOPERATIVE_COMPLICATIONS",
    "PREVENTION",
}
_GUIDELINE_EVIDENCE = re.compile(
    r"\b(?:guidelines?|consensus|standards?|latest|current recommendations?)\b|"
    r"指南|共识|标准|最新(?:推荐|指南)",
    re.I,
)
_RESEARCH_EVIDENCE = re.compile(
    r"\b(?:evidence|literature|references?|research)\b|循证|证据|文献|外部资料",
    re.I,
)
_CLASSIFICATION_EVIDENCE = re.compile(r"\b(?:ICD|disease code|classification)\b|疾病编码", re.I)
_COMPREHENSIVE = re.compile(
    r"\b(?:comprehensive|complete|full|overall)\s+(?:analysis|assessment|review)\b|"
    r"全面(?:分析|评估|诊疗)|综合(?:分析|评估|诊疗)",
    re.I,
)
_FOCUSED_REQUEST = re.compile(
    r"\b(?:what is|which|first.?line|mechanism|how does)\b|首选|是什么|哪一|机制|作用",
    re.I,
)
_INITIAL_MANAGEMENT = re.compile(
    r"\b(?:initial|preliminary|first.?step)\s+(?:management|treatment|plan)\b|"
    r"初步(?:处理|治疗|管理|方案)",
    re.I,
)


@dataclass(frozen=True, slots=True)
class TaskComplexityProfile:
    breadth: str
    deliverable_count: int
    requires_diagnosis: bool
    requires_differential: bool
    requires_management: bool
    requires_external_evidence: bool
    external_evidence_kind: str
    requires_cross_role_reasoning: bool
    requires_multi_agent: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ResponseProfile:
    name: str
    objective: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def build_complexity_profile(
    question: str, contract: AnswerContract
) -> TaskComplexityProfile:
    deliverables = set(contract.requested_deliverables)
    requires_diagnosis = bool(deliverables & _DIAGNOSIS)
    requires_differential = bool(deliverables & _DIFFERENTIAL)
    requires_management = bool(deliverables & _MANAGEMENT)
    requires_tests = bool(deliverables & _TESTS)
    if _GUIDELINE_EVIDENCE.search(question):
        external_evidence_kind = "guideline"
    elif _CLASSIFICATION_EVIDENCE.search(question):
        external_evidence_kind = "classification"
    elif _RESEARCH_EVIDENCE.search(question):
        external_evidence_kind = "research"
    else:
        external_evidence_kind = "none"
    requires_external_evidence = external_evidence_kind != "none"
    has_general_clinical_deliverable = "COMPREHENSIVE_CASE_ANALYSIS" in deliverables
    clinical_roles = sum(
        (
            requires_diagnosis
            or requires_differential
            or requires_tests
            or has_general_clinical_deliverable,
            requires_management,
            requires_external_evidence,
        )
    )
    comprehensive_request = bool(_COMPREHENSIVE.search(question))
    low_scope_initial_pathway = (
        requires_diagnosis
        and requires_management
        and not requires_differential
        and not requires_tests
        and not requires_external_evidence
        and len(deliverables) <= 2
        and bool(_INITIAL_MANAGEMENT.search(question))
    )
    requires_cross_role_reasoning = (
        clinical_roles >= 2 and not low_scope_initial_pathway
    )
    covers_full_pathway = (
        requires_diagnosis
        and requires_differential
        and requires_tests
        and requires_management
    )
    if comprehensive_request or covers_full_pathway:
        breadth = "comprehensive"
    elif has_general_clinical_deliverable and not _FOCUSED_REQUEST.search(question):
        breadth = "moderate"
    elif len(deliverables) > 1 or requires_cross_role_reasoning:
        breadth = "moderate"
    else:
        breadth = "focused"
    requires_multi_agent = breadth == "comprehensive" and requires_cross_role_reasoning
    return TaskComplexityProfile(
        breadth=breadth,
        deliverable_count=len(deliverables),
        requires_diagnosis=requires_diagnosis,
        requires_differential=requires_differential,
        requires_management=requires_management,
        requires_external_evidence=requires_external_evidence,
        external_evidence_kind=external_evidence_kind,
        requires_cross_role_reasoning=requires_cross_role_reasoning,
        requires_multi_agent=requires_multi_agent,
    )


def build_response_profile(complexity: TaskComplexityProfile) -> ResponseProfile:
    if complexity.breadth == "focused":
        return ResponseProfile(
            "focused",
            "Answer only the requested deliverables, with essential rationale and safety advice.",
        )
    if complexity.breadth == "comprehensive":
        return ResponseProfile(
            "comprehensive",
            "Provide a complete structured answer for every requested deliverable.",
        )
    return ResponseProfile(
        "standard",
        "Answer every requested deliverable with concise explanation and necessary context.",
    )


def preferred_worker(deliverable: str, *, external_evidence: bool = False) -> str:
    if external_evidence and deliverable == "COMPREHENSIVE_CASE_ANALYSIS":
        return "research_agent"
    if deliverable in _MANAGEMENT:
        return "consultation_agent"
    return "diagnostic_agent"


def tool_capabilities(
    complexity: TaskComplexityProfile,
) -> dict[str, set[str]]:
    capabilities = {
        "diagnostic_agent": {"analyze_symptoms", "assess_risk"},
        "consultation_agent": {"assess_risk"},
        "research_agent": set(),
    }
    if complexity.requires_external_evidence:
        if complexity.external_evidence_kind == "guideline":
            for tools in capabilities.values():
                tools.add("clinical_guideline")
        elif complexity.external_evidence_kind == "classification":
            capabilities["diagnostic_agent"].add("disease_code")
            capabilities["research_agent"].add("disease_code")
        else:
            capabilities["diagnostic_agent"].add("search_knowledge")
            capabilities["consultation_agent"].add("search_knowledge")
            capabilities["research_agent"].update(
                {"deep_research", "search_knowledge"}
            )
    return capabilities
