from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class EvidenceCard:
    evidence_id: str
    title: str | None
    source: str
    section: str | None
    score: float
    text_preview: str


@dataclass(slots=True)
class PresentationResponse:
    headline: str
    plain_language_summary: str
    professional_answer: str
    evidence_cards: list[EvidenceCard] = field(default_factory=list)
    execution_summary: dict[str, Any] = field(default_factory=dict)
    disclaimer: str = "仅供医学信息与病例分析演示，不能替代专业医生诊疗。"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
