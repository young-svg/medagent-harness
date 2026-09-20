from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from medagent.context.models import Finding


@dataclass(slots=True)
class EvidenceLedger:
    findings: list[Finding] = field(default_factory=list)
    source_conflicts: list[str] = field(default_factory=list)

    def validate(self) -> None:
        ids = [item.finding_id for item in self.findings]
        if len(ids) != len(set(ids)):
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
    chunks = [part.strip() for part in description.replace("；", ";").split(";") if part.strip()]
    if not chunks and description.strip():
        chunks = [description.strip()]
    return EvidenceLedger(
        findings=[
            Finding(finding_id=f"E{index}", source_text=text)
            for index, text in enumerate(chunks, 1)
        ]
    )
