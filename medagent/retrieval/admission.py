from __future__ import annotations

import math

from medagent.retrieval.evidence import EvidenceItem


def admit_evidence(
    items: list[EvidenceItem], threshold: float = 0.63, maximum: int = 5
) -> list[EvidenceItem]:
    admitted_count = 0
    seen: set[str] = set()
    for item in sorted(items, key=lambda value: (
        -value.score if type(value.score) in {int, float} and math.isfinite(value.score)
        else float("inf"), value.rank,
    )):
        item.admitted = False
        if item.admission_reason == "instruction_spoof_quarantined":
            continue
        identifier = item.evidence_id
        if (not isinstance(identifier, str) or not 1 <= len(identifier) <= 256
                or any(not c.isprintable() or c.isspace() or c in "[]" for c in identifier)
                or not isinstance(item.text, str) or not item.text.strip()
                or type(item.score) not in {int, float} or not math.isfinite(item.score)
                or not any((isinstance(value, str) and value.strip()) or type(value) is int
                           for value in (item.source, item.url, item.source_id, item.document_id))):
            item.admission_reason = "invalid_evidence"
            continue
        if item.evidence_id in seen:
            item.admission_reason = "duplicate_evidence_id"
            continue
        seen.add(item.evidence_id)
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
