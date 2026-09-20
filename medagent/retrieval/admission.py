from __future__ import annotations

from medagent.retrieval.evidence import EvidenceItem


def admit_evidence(
    items: list[EvidenceItem], threshold: float = 0.63, maximum: int = 5
) -> list[EvidenceItem]:
    admitted_count = 0
    for item in sorted(items, key=lambda value: (-value.score, value.rank)):
        if item.score >= threshold and admitted_count < maximum:
            item.admitted = True
            item.admission_reason = "score_at_or_above_threshold"
            admitted_count += 1
        else:
            item.admitted = False
            item.admission_reason = (
                "maximum_admitted_evidence_reached"
                if admitted_count >= maximum
                else "score_below_threshold"
            )
    return items
