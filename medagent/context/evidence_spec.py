"""Validated evidence declarations for trusted frozen execution, not model output."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from medagent.context.request_spec import RequestSpec

SOURCE_CATEGORIES = frozenset({"classification", "research", "guideline", "external"})


@dataclass(frozen=True, slots=True)
class EvidenceRequirement:
    request_item_id: str
    required: bool
    source_category: str
    acceptable_source_categories: tuple[str, ...]
    execution: str
    upstream_request_item_ids: tuple[str, ...]
    topic: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = {"request_item_id": self.request_item_id, "required": self.required,
                 "source_category": self.source_category,
                 "acceptable_source_categories": list(self.acceptable_source_categories),
                 "execution": self.execution,
                 "upstream_request_item_ids": list(self.upstream_request_item_ids),
                 "requires_admission_delivery_citation": True}
        if self.topic is not None:
            value["topic"] = self.topic
        return value

    def accepts_source(self, receipt: dict[str, Any]) -> bool:
        from medagent.planning.complexity import EVIDENCE_TOOLS

        return (receipt["tool"] in EVIDENCE_TOOLS[self.source_category]
                and receipt.get("source_kind") in self.acceptable_source_categories)

    def accepts_delivery(self, receipt: dict[str, Any]) -> bool:
        if not self.accepts_source(receipt):
            return False
        if self.execution == "retrieve":
            return (not receipt.get("handoff_event_id")
                    and receipt.get("origin_request_item_id") == self.request_item_id)
        return (bool(receipt.get("handoff_event_id"))
                and receipt.get("upstream_request_item_id") in self.upstream_request_item_ids)


def normalize_evidence_requirements(
    spec: RequestSpec, raw: Any,
) -> tuple[EvidenceRequirement, ...]:
    if not isinstance(raw, (list, tuple)):
        raise ValueError("frozen evidence requirements must be a list or tuple")
    items = {item.id: item for item in spec.items}
    result = {}
    fields = {"request_item_id", "required", "source_category", "acceptable_source_categories",
              "execution", "upstream_request_item_ids"}
    for row in raw:
        if not isinstance(row, dict) or not fields <= row.keys() or set(row) - fields - {
            "topic", "requires_admission_delivery_citation",
        }:
            raise ValueError("invalid frozen evidence schema")
        rq, category = row["request_item_id"], row["source_category"]
        if (type(rq) is not str or rq not in items or rq in result
                or type(category) is not str or category not in SOURCE_CATEGORIES
                or type(row["required"]) is not bool or row["required"] != items[rq].required
                or type(row["execution"]) is not str
                or row["execution"] not in {"retrieve", "reuse_only"}
                or row.get("requires_admission_delivery_citation", True) is not True):
            raise ValueError("invalid frozen evidence RQ, category or priority")
        accepted, upstream = row["acceptable_source_categories"], row["upstream_request_item_ids"]
        if (not isinstance(accepted, (list, tuple)) or not accepted
                or any(type(k) is not str or k not in SOURCE_CATEGORIES for k in accepted)
                or len(set(accepted)) != len(accepted)
                or category != "external" and set(accepted) != {category}
                or not isinstance(upstream, (list, tuple))
                or any(type(u) is not str or u not in items or u == rq for u in upstream)
                or len(set(upstream)) != len(upstream)
                or (row["execution"] == "reuse_only") != bool(upstream)):
            raise ValueError("invalid frozen evidence categories or upstream mapping")
        topic = row.get("topic")
        if topic is not None and (type(topic) is not str or not topic.strip()):
            raise ValueError("invalid frozen evidence topic")
        result[rq] = EvidenceRequirement(rq, row["required"], category, tuple(sorted(accepted)),
                                         row["execution"], tuple(sorted(upstream)), topic)
    for requirement in result.values():
        for upstream in requirement.upstream_request_item_ids:
            source = result.get(upstream)
            if (source is None or requirement.required and not source.required
                    or not set(source.acceptable_source_categories)
                    & set(requirement.acceptable_source_categories)):
                raise ValueError("incompatible frozen upstream evidence")
    pending, seen = set(result), set()
    while pending:
        ready = {rq for rq in pending if set(result[rq].upstream_request_item_ids) <= seen}
        if not ready:
            raise ValueError("cyclic frozen evidence dependencies")
        pending -= ready
        seen |= ready
    return tuple(result[item.id] for item in spec.items if item.id in result)
