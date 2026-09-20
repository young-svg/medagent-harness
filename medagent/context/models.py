from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class CoverageItem:
    item: str
    priority: str = "MUST"
    covered: bool = False

    def validate(self) -> None:
        if not self.item.strip():
            raise ValueError("coverage item cannot be empty")
        if self.priority not in {"MUST", "OPTIONAL"}:
            raise ValueError("priority must be MUST or OPTIONAL")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> CoverageItem:
        item = cls(**value)
        item.validate()
        return item


@dataclass(slots=True)
class Finding:
    finding_id: str
    source_text: str
    attribute: str = "statement"
    value: str = "reported"
    certainty: str = "explicit"
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.finding_id or not self.source_text.strip():
            raise ValueError("finding_id and source_text are required")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Finding:
        finding = cls(**value)
        finding.validate()
        return finding
