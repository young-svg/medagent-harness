from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from medagent.context.contract import infer_deliverable_ids

_NUMBERED_ITEM = re.compile(
    r"(?m)^[ \t]*(?:\d{1,3}\s*[.)、．）]|[（(]\s*\d{1,3}\s*[)）]|[①-⑳])\s*"
)
_BULLET_ITEM = re.compile(r"(?m)^[ \t]*(?:[-*+•▪◦]|·)\s+")
_OPTIONAL_PREFIX = re.compile(r"^(?:optional|可选|选答)\s*[:：-]?\s*", re.I)
_OPEN_WORLD_INTENT = re.compile(
    r"\b(?:benefits?|advantages?|etiology|aetiology|mechanism|manifestations?)\b|"
    r"益处|好处|获益|病因|发病机制|临床表现|为什么(?:会)?发生",
    re.I,
)


@dataclass(frozen=True, slots=True)
class RequestItem:
    id: str
    text: str
    required: bool
    order: int
    source_span: str
    semantic_type: str | None = None

    def validate(self) -> None:
        if not self.id.strip() or not self.text.strip() or not self.source_span.strip():
            raise ValueError("request item id, text, and source_span are required")
        if self.order < 1:
            raise ValueError("request item order must be positive")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> RequestItem:
        item = cls(
            id=str(value["id"]),
            text=str(value["text"]),
            required=bool(value.get("required", True)),
            order=int(value["order"]),
            source_span=str(value["source_span"]),
            semantic_type=(
                str(value["semantic_type"])
                if value.get("semantic_type") is not None
                else None
            ),
        )
        item.validate()
        return item


@dataclass(frozen=True, slots=True)
class RequestSpec:
    items: list[RequestItem]

    def validate(self) -> None:
        if not self.items:
            raise ValueError("request spec requires at least one item")
        ids: set[str] = set()
        orders: set[int] = set()
        for item in self.items:
            item.validate()
            if item.id in ids or item.order in orders:
                raise ValueError("request item ids and orders must be unique")
            ids.add(item.id)
            orders.add(item.order)

    @property
    def required_item_ids(self) -> list[str]:
        return [item.id for item in self.items if item.required]

    def to_dict(self) -> dict[str, Any]:
        return {"items": [item.to_dict() for item in self.items]}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> RequestSpec:
        spec = cls([RequestItem.from_dict(item) for item in value.get("items", [])])
        spec.validate()
        return spec


def _structured_spans(question: str) -> list[tuple[str, str]]:
    """Return conservative, source-backed item text and spans."""

    for marker in (_NUMBERED_ITEM, _BULLET_ITEM):
        matches = list(marker.finditer(question))
        if len(matches) < 2:
            continue
        spans: list[tuple[str, str]] = []
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(question)
            source_span = question[match.start() : end].strip()
            text = question[match.end() : end].strip()
            if text:
                spans.append((text, source_span))
        if len(spans) >= 2:
            return spans

    lines = [line.strip() for line in question.splitlines() if line.strip()]
    if len(lines) >= 2 and all(line.endswith(("?", "？")) for line in lines):
        return [(line, line) for line in lines]
    return []


def build_request_spec(question: str) -> RequestSpec:
    """Extract only deterministic request boundaries, otherwise preserve the whole question."""

    if not question.strip():
        raise ValueError("question is required")
    spans = _structured_spans(question)
    if not spans:
        whole = question.strip()
        spans = [(whole, whole)]

    items: list[RequestItem] = []
    for order, (raw_text, source_span) in enumerate(spans, 1):
        optional = bool(_OPTIONAL_PREFIX.match(raw_text))
        text = _OPTIONAL_PREFIX.sub("", raw_text, count=1).strip() if optional else raw_text
        semantic_types = [] if _OPEN_WORLD_INTENT.search(text) else infer_deliverable_ids(text)
        items.append(
            RequestItem(
                id=f"RQ{order}",
                text=text,
                required=not optional,
                order=order,
                source_span=source_span,
                semantic_type=semantic_types[0] if semantic_types else "UNKNOWN",
            )
        )
    spec = RequestSpec(items)
    spec.validate()
    return spec
