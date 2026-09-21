from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RetrievalConfig:
    generic_collection: str = "clinical_knowledge"
    special_collection: str = "clinical_guidelines"
    top_k: int = 5
    admission_threshold: float = 0.63
    max_admitted_evidence: int = 5
    max_evidence_chars: int = 1200
