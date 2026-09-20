from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class EvidenceItem:
    evidence_id: str
    document_id: str | int | None
    source_id: str | None
    title: str | None
    section: str | None
    source: str | None
    url: str | None
    text: str
    score: float
    rank: int
    metadata: dict[str, Any] = field(default_factory=dict)
    admitted: bool = False
    admission_reason: str = "not_evaluated"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class EvidenceBundle:
    query: str
    collection: str
    items: list[EvidenceItem] = field(default_factory=list)

    @property
    def admitted_items(self) -> list[EvidenceItem]:
        return [item for item in self.items if item.admitted]

    def compact(self, max_chars: int = 1200) -> list[dict[str, Any]]:
        return [
            {
                "evidence_id": item.evidence_id,
                "title": item.title,
                "source": item.source,
                "section": item.section,
                "text": item.text[:max_chars],
                "score": item.score,
            }
            for item in self.admitted_items
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "collection": self.collection,
            "items": [item.to_dict() for item in self.items],
        }
