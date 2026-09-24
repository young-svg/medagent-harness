"""Local demo integrity checks; no provider or browser API calls."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" / "demo_cases"
ANSWER_SOURCE = json.loads(
    (ROOT / "web" / "src" / "demoFinalAnswers.json").read_text(encoding="utf-8")
)
FILE_NAMES = {
    "demo-01-simple-single": "demo_01_simple_single.json",
    "demo-02-multi-agent": "demo_02_multi_agent.json",
    "demo-03-guideline-rag": "demo_03_guideline_rag.json",
    "demo-04-memory-followup": "demo_04_memory_followup.json",
}


@pytest.mark.parametrize("demo_id", FILE_NAMES)
def test_demo_has_exact_full_professional_answer(demo_id: str) -> None:
    fixture = json.loads((EXAMPLES / FILE_NAMES[demo_id]).read_text(encoding="utf-8"))
    detail = fixture["presentation"]["clinical_detail"]
    assert detail == ANSWER_SOURCE[demo_id]
    assert len(detail) > 700
    assert fixture["source_run_id"]
    assert fixture["source_kind"] == "completed_real_agent_final_answer"
    user_text = "\n".join(
        fixture["presentation"][field]
        for field in ("direct_answer", "plain_language", "clinical_detail")
    ).casefold()
    assert "demo fixture" not in user_text
    assert "synthetic evidence" not in user_text
    assert "no api call" not in user_text


def test_demo_02_direct_answer_covers_all_four_deliverables() -> None:
    fixture = json.loads((EXAMPLES / FILE_NAMES["demo-02-multi-agent"]).read_text(encoding="utf-8"))
    direct = fixture["presentation"]["direct_answer"]
    assert all(
        label in direct
        for label in ("最可能诊断", "主要鉴别", "进一步检查", "治疗与随访")
    )
    assert len(fixture["presentation"]["clinical_detail"]) > 1000


def test_demo_03_evidence_is_from_captured_milvus_run() -> None:
    path = EXAMPLES / FILE_NAMES["demo-03-guideline-rag"]
    fixture = json.loads(path.read_text(encoding="utf-8"))
    evidence = fixture["developer"]["evidence"]
    assert evidence["status"] == "AVAILABLE"
    assert len(evidence["cards"]) >= 1
    assert all(card["evidence_id"].startswith("milvus-") for card in evidence["cards"])
    assert all(card["source"] != "Demo Clinical Guideline Fixture" for card in evidence["cards"])
