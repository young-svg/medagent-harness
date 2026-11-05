from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from medagent.context.evidence_spec import EvidenceRequirement


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


def declared_source_kind(item: EvidenceItem, tool: str) -> str:
    """Catalogue declarations, never an LLM assertion or a publication authenticity check."""
    aliases = {
        "research": "research", "paper": "research", "research_paper": "research",
        "journal_article": "research", "论文": "research",
        "guideline": "guideline", "clinical_guideline": "guideline", "指南": "guideline",
        "classification": "classification", "disease_code": "classification",
    }
    for key in ("source_kind", "document_type", "publication_type", "type"):
        value = str(item.metadata.get(key, "")).strip().casefold()
        if value in aliases:
            return aliases[value]
        if value and key != "type":
            return "external"
    # Specialized tools have a configured corpus category. A generic knowledge
    # search has no such paper guarantee; explicit papers need a catalogue label.
    return {"clinical_guideline": "guideline", "disease_code": "classification"}.get(
        tool, "external",
    )


@dataclass(slots=True)
class EvidenceBundle:
    query: str
    collection: str
    items: list[EvidenceItem] = field(default_factory=list)
    # Append-only retrieval provenance; query/collection remain legacy last-call fields.
    retrievals: list[dict[str, Any]] = field(default_factory=list)
    # Program-created receipts: one retrieval/transfer, one target RQ, one source.
    deliveries: list[dict[str, Any]] = field(default_factory=list)
    accepted_by_task: dict[tuple[str, str], set[str]] = field(default_factory=dict)
    accepted_by_request: dict[str, set[str]] = field(default_factory=dict)
    worker_checks: dict[str, dict[str, object]] = field(default_factory=dict)
    case_description: str = ""
    evidence_requirements: tuple[EvidenceRequirement, ...] | None = None

    def requirement_for(self, request_id: str) -> EvidenceRequirement | None:
        return next((r for r in self.evidence_requirements or ()
                     if r.request_item_id == request_id), None)

    def receipts(self, request_id: str, subtask_id: str | None = None) -> list[dict[str, Any]]:
        return [r for r in self.deliveries if r["request_item_id"] == request_id
                and (subtask_id is None or r["subtask_id"] == subtask_id)]

    def available(self, subtask_id: str, request_id: str) -> set[str]:
        return {r["evidence_id"] for r in self.receipts(request_id, subtask_id)}

    @property
    def admitted_items(self) -> list[EvidenceItem]:
        return [item for item in self.items if item.admitted]

    def compact(self, max_chars: int = 1200) -> list[dict[str, Any]]:
        return [
            {
                "evidence_id": item.evidence_id,
                "title": item.title,
                "source": item.source,
                "document_id": item.document_id,
                "source_id": item.source_id,
                "url": item.url,
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
            "retrievals": self.retrievals,
            "queries": list(dict.fromkeys(r["query"] for r in self.retrievals)),
            "collections": list(dict.fromkeys(r["collection"] for r in self.retrievals)),
        }
