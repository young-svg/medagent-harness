from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from medagent.context.models import Finding


@dataclass(slots=True)
class EvidenceLedger:
    findings: list[Finding] = field(default_factory=list)
    source_conflicts: list[str] = field(default_factory=list)

    def validate(self) -> None:
        identifiers = [item.finding_id for item in self.findings]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("finding IDs must be unique")
        for item in self.findings:
            item.validate()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> EvidenceLedger:
        ledger = cls(
            findings=[Finding.from_dict(item) for item in value.get("findings", [])],
            source_conflicts=list(value.get("source_conflicts", [])),
        )
        ledger.validate()
        return ledger


def build_evidence_ledger(description: str) -> EvidenceLedger:
    chunks = [part.strip() for part in re.split(r"[;；\n]+", description) if part.strip()]
    findings = []
    for index, text in enumerate(chunks, 1):
        lower = text.casefold()
        attribute = "question" if text.endswith(("?", "？")) else "statement"
        certainty = (
            "conditional" if any(x in lower for x in (" if ", "may ", "可能", "若")) else "explicit"
        )
        findings.append(Finding(f"E{index}", text, attribute=attribute, certainty=certainty))
    ledger = EvidenceLedger(findings=findings)
    ledger.validate()
    return ledger
