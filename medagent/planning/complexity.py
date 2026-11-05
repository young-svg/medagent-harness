from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from medagent.context.contract import AnswerContract
from medagent.context.evidence_spec import EvidenceRequirement
from medagent.context.request_spec import RequestSpec

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
    r"\b(?:guidelines?|consensus|published standards?|current recommendations?)\b|"
    r"指南|共识|公开标准|最新(?:推荐|指南)",
    re.I,
)
_RESEARCH_EVIDENCE = re.compile(
    r"\b(?:literature|research (?:papers?|evidence)|papers?|published studies)\b|文献|论文",
    re.I,
)
_EXTERNAL_SOURCE = re.compile(
    r"\b(?:external (?:sources?|materials?|information|evidence)|references?|citations?)\b|"
    r"外部(?:资料|材料|信息|证据|来源)|参考资料|循证", re.I,
)
_SOURCE_ACTION = re.compile(
    r"\b(?:retrieve|search|find|consult|cite|use|review|provide|identify|list|include|give)\b|"
    r"检索|查阅|查询|查找|参考|引用|提供|给出|标注|注明|依据|根据", re.I,
)
_LOCAL_EXPLANATION = re.compile(
    r"\b(?:reference range|what does (?:the )?word|meaning of (?:the )?word)\b|"
    r"(?:解释|说明).{0,20}(?:词义|含义)|"
    r"(?:已提供|给定|病例中).{0,12}(?:内容|资料|范围|参考值)", re.I,
)
_REUSE_SOURCE = re.compile(
    r"\b(?:those|these|above|upstream)\b.*\b(?:guidelines?|sources?|evidence|papers?|result)\b|"
    r"(?:上述|上游|这些|该).{0,8}(?:指南|来源|证据|文献|结果)", re.I,
)
_CLASSIFICATION_EVIDENCE = re.compile(
    r"\b(?:ICD|disease codes?|classification codes?)\b|疾病编码", re.I,
)
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


def evidence_requirements(text: str) -> set[str]:
    """Recognize explicit source requests, not ordinary diagnostic 'evidence'.

    Operates on the source-backed positive RequestItems produced by W1. This
    conservative lexical policy is not a general semantic fact checker.
    """
    if re.match(r"\s*(?:(?:do not|don't|no need to)\b|不要|无需|禁止)", text, re.I):
        return set()
    # Mentioning a source term in a definition or supplied case is not a request
    # to consult that source. A separate explicit external action still wins.
    explicit_lookup = bool(re.search(
        r"\b(?:retrieve|search|find|consult|cite)\b|检索|查阅|查找|引用", text, re.I,
    ))
    if _LOCAL_EXPLANATION.search(text) and not explicit_lookup:
        return set()
    kinds = set()
    if _GUIDELINE_EVIDENCE.search(text):
        kinds.add("guideline")
    if _CLASSIFICATION_EVIDENCE.search(text):
        kinds.add("classification")
    if _RESEARCH_EVIDENCE.search(text):
        kinds.add("research")
    if not kinds and (_EXTERNAL_SOURCE.search(text) and _SOURCE_ACTION.search(text)
                      or re.search(r"\b(?:retrieve|search|find|cite)\b.*\bevidence\b|"
                                   r"(?:检索|查找)证据", text, re.I)):
        kinds.add("external")
    return kinds


def permits_handoff_reuse(text: str) -> bool:
    """Only explicit reuse of upstream material can transfer a different RQ's source."""
    fresh_lookup = re.search(
        r"\b(?:retrieve|search|find|consult)\b|检索|查阅|查询|查找", text, re.I,
    )
    return bool(_REUSE_SOURCE.search(text)) and not fresh_lookup


EVIDENCE_TOOLS = {
    "guideline": {"clinical_guideline"},
    "classification": {"disease_code"},
    "research": {"search_knowledge", "deep_research"},
    "external": {"clinical_guideline", "disease_code", "search_knowledge", "deep_research"},
}


def evidence_requirement(text: str) -> str:
    kinds = evidence_requirements(text)
    return "mixed" if len(kinds) > 1 else next(iter(kinds), "none")


def build_complexity_profile(
    question: str, contract: AnswerContract, request_spec: RequestSpec | None = None,
    *, frozen_evidence_requirements: tuple[EvidenceRequirement, ...] | None = None,
) -> TaskComplexityProfile:
    deliverables = set(contract.requested_deliverables)
    requires_diagnosis = bool(deliverables & _DIAGNOSIS)
    requires_differential = bool(deliverables & _DIFFERENTIAL)
    requires_management = bool(deliverables & _MANAGEMENT)
    requires_tests = bool(deliverables & _TESTS)
    if frozen_evidence_requirements is None:
        kinds = set().union(*(evidence_requirements(item.text) for item in request_spec.items)) \
            if request_spec else evidence_requirements(question)
    else:
        kinds = {r.source_category for r in frozen_evidence_requirements}
    external_evidence_kind = "mixed" if len(kinds) > 1 else next(iter(kinds), "none")
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
        if complexity.external_evidence_kind in {"guideline", "mixed"}:
            for tools in capabilities.values():
                tools.add("clinical_guideline")
        if complexity.external_evidence_kind in {"classification", "mixed"}:
            capabilities["diagnostic_agent"].add("disease_code")
            capabilities["research_agent"].add("disease_code")
        if complexity.external_evidence_kind in {"research", "external", "mixed"}:
            capabilities["diagnostic_agent"].add("search_knowledge")
            capabilities["consultation_agent"].add("search_knowledge")
            capabilities["research_agent"].update(
                {"deep_research", "search_knowledge"}
            )
    return capabilities
