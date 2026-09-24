from __future__ import annotations

import pytest

from medagent.context.contract import build_answer_contract
from medagent.context.evidence_ledger import build_evidence_ledger
from medagent.presentation.adapter import PresentationAdapter, extract_direct_answer
from medagent.retrieval.evidence import EvidenceBundle, EvidenceItem


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("诊断：\n胃食管反流病", "胃食管反流病"),
        (
            "免责声明：以下内容仅供参考。\n\n诊断考虑：食管下段恶性肿瘤，需病理确认。",
            "食管下段恶性肿瘤，需病理确认。",
        ),
        (
            "【综合病例分析】\n\n说明：以下为方向性分析。\n\n诊断考虑：\n胃食管交界部恶性肿瘤。",
            "胃食管交界部恶性肿瘤。",
        ),
        (
            "【综合病例分析：高血压生活方式管理】\n\n"
            "说明：以下为一般建议。\n\n"
            "一、核心生活方式措施\n1. 限盐并采用健康膳食。",
            "1. 限盐并采用健康膳食。",
        ),
        (
            "# 高血压患者生活方式管理方案（基于指南）\n\n"
            "**总体原则**：生活方式干预应贯穿治疗全程，并与必要的药物治疗并行。",
            "总体原则：生活方式干预应贯穿治疗全程，并与必要的药物治疗并行。",
        ),
        (
            "**RQ1：结合指南说明高血压患者生活方式管理方案（综合管理分析）**\n\n"
            "以下依据相关指南提出一般建议，不代表已经实施。\n\n"
            "**一、总体原则**\n"
            "- 生活方式干预应贯穿治疗全程，并与必要的药物治疗并行。",
            "生活方式干预应贯穿治疗全程，并与必要的药物治疗并行。",
        ),
    ],
)
def test_direct_answer_extraction_skips_disclaimers_and_headings(
    answer: str, expected: str
) -> None:
    assert extract_direct_answer(answer) == expected


def test_direct_answer_skips_numbered_diagnosis_heading() -> None:
    answer = """以下内容仅供参考，不能替代面诊。

## 1. 最可能诊断及依据（RQ1）

患者的警示症状首先考虑食管恶性病变，需要病理确认。"""

    assert extract_direct_answer(answer) == "患者的警示症状首先考虑食管恶性病变，需要病理确认。"


def test_presentation_exposes_direct_answer_and_honest_plain_fallback() -> None:
    answer = "免责声明：仅供参考。\n\n最可能诊断：胃食管反流病。"
    response = PresentationAdapter().adapt(
        answer,
        build_answer_contract("请分析最可能诊断。"),
        build_evidence_ledger("45岁男性，反酸。"),
        EvidenceBundle("", "", []),
        {},
    ).to_dict()

    assert response["direct_answer"] == "胃食管反流病。"
    assert response["headline"] == "胃食管反流病。"
    assert response["plain_language_summary"] == ""
    assert response["professional_answer"] == answer


def test_presentation_builds_retrieval_diagnostics_without_changing_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEDICAL_KB_ENABLED", "true")
    monkeypatch.setenv("MEDICAL_KB_SPECIAL_COLLECTION", "medical_knowledge")
    evidence = EvidenceItem(
        evidence_id="ev-1",
        document_id="doc-1",
        source_id="source-1",
        title="Hypertension",
        section="lifestyle",
        source="Clinical guideline database",
        url=None,
        text="Reduce sodium intake.",
        score=0.86,
        rank=1,
        admitted=True,
        admission_reason="score_threshold_met",
    )
    developer = {
        "retrieval": {
            "query": "hypertension lifestyle",
            "collection": "clinical_guidelines",
            "retrieved_count": 1,
            "admitted_evidence_ids": ["ev-1"],
        },
        "trace_events": [
            {
                "event_type": "retrieval_query",
                "payload": {
                    "query": "hypertension lifestyle",
                    "collection": "clinical_guidelines",
                    "top_k": 5,
                },
            },
            {
                "event_type": "retrieval_result",
                "payload": {
                    "raw_candidates": [evidence.to_dict()],
                    "admission": [
                        {"evidence_id": "ev-1", "admitted": True, "score": 0.86}
                    ],
                },
            },
        ],
    }

    response = PresentationAdapter().adapt(
        "推荐：限制钠盐摄入。",
        build_answer_contract("请结合指南说明高血压管理。"),
        build_evidence_ledger("高血压患者。"),
        EvidenceBundle("hypertension lifestyle", "clinical_guidelines", [evidence]),
        {},
        developer,
    ).to_dict()
    summary = response["execution_summary"]["retrieval_summary"]

    assert summary == {
        "query": "hypertension lifestyle",
        "queries": ["hypertension lifestyle"],
        "query_count": 1,
        "logical_collection": "clinical_guidelines",
        "physical_collection": "medical_knowledge",
        "top_k": 5,
        "candidate_count": 1,
        "admitted_count": 1,
        "unique_evidence_count": 1,
        "top_score": 0.86,
    }
    assert response["evidence_cards"][0]["score"] == 0.86
