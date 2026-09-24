from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class EvidenceCard:
    evidence_id: str
    title: str | None
    source: str
    section: str | None
    score: float | None
    text_preview: str


@dataclass(slots=True)
class PresentationResponse:
    headline: str
    plain_language_summary: str
    professional_answer: str
    direct_answer: str | None = None
    direct_answer_title: str | None = None
    direct_answer_items: list[str] = field(default_factory=list)
    direct_answer_sections: list[dict[str, Any]] = field(default_factory=list)
    plain_explanation: list[str] = field(default_factory=list)
    evidence_cards: list[EvidenceCard] = field(default_factory=list)
    execution_summary: dict[str, Any] = field(default_factory=dict)
    disclaimer: str = (
        "For clinical information and case-analysis demonstration only; "
        "it does not replace qualified medical care."
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
