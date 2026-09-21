from __future__ import annotations

import re
from typing import Any

from medagent.context.contract import AnswerContract
from medagent.context.evidence_ledger import EvidenceLedger
from medagent.presentation.models import EvidenceCard, PresentationResponse
from medagent.retrieval.evidence import EvidenceBundle


class PresentationAdapter:
    """Build three-view data without adding clinical claims."""

    def adapt(
        self,
        final_answer: str,
        contract: AnswerContract,
        ledger: EvidenceLedger,
        evidence: EvidenceBundle,
        trace_metadata: dict[str, Any],
        developer: dict[str, Any] | None = None,
    ) -> PresentationResponse:
        paragraphs = [item.strip() for item in re.split(r"\n\s*\n", final_answer) if item.strip()]
        cards = [
            EvidenceCard(
                item.evidence_id,
                item.title,
                item.source or "Source metadata unavailable",
                item.section,
                item.score,
                item.text[:300],
            )
            for item in evidence.admitted_items
        ]
        execution = {
            **trace_metadata,
            "runtime_mode": "native",
            "contract": contract.to_dict(),
            "ledger": ledger.to_dict(),
            **(developer or {}),
        }
        return PresentationResponse(
            headline=paragraphs[0].splitlines()[0] if paragraphs else "Analysis unavailable",
            plain_language_summary=paragraphs[0] if paragraphs else "",
            professional_answer=final_answer,
            evidence_cards=cards,
            execution_summary=execution,
        )
