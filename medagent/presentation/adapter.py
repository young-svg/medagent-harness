from __future__ import annotations

import re
from typing import Any

from medagent.context.contract import AnswerContract
from medagent.context.evidence_ledger import EvidenceLedger
from medagent.presentation.models import EvidenceCard, PresentationResponse
from medagent.retrieval.evidence import EvidenceBundle


class PresentationAdapter:
    """Deterministic view model; it never introduces new clinical facts."""

    def adapt(
        self,
        final_answer: str,
        contract: AnswerContract,
        ledger: EvidenceLedger,
        evidence: EvidenceBundle,
        trace_metadata: dict[str, Any],
    ) -> PresentationResponse:
        paragraphs = [item.strip() for item in re.split(r"\n\s*\n", final_answer) if item.strip()]
        headline = (
            paragraphs[0].splitlines()[0].strip("【】# ") if paragraphs else "Analysis unavailable"
        )
        summary = paragraphs[0] if paragraphs else ""
        cards = [
            EvidenceCard(
                evidence_id=item.evidence_id,
                title=item.title,
                source=item.source or "Source metadata unavailable",
                section=item.section,
                score=item.score,
                text_preview=item.text[:300],
            )
            for item in evidence.admitted_items
        ]
        return PresentationResponse(
            headline=headline,
            plain_language_summary=summary,
            professional_answer=final_answer,
            evidence_cards=cards,
            execution_summary={
                **trace_metadata,
                "answer_contract": contract.to_dict(),
                "evidence_ledger": ledger.to_dict(),
            },
        )
