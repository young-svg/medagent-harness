from __future__ import annotations

from dataclasses import dataclass

from medagent.retrieval.config import RetrievalConfig


@dataclass(frozen=True, slots=True)
class CollectionDecision:
    collection: str
    reason: str
    fallback_collection: str | None


class CollectionRouter:
    SPECIAL = {"clinical_guideline", "disease_code", "recommend_lifestyle"}

    def __init__(self, config: RetrievalConfig | None = None) -> None:
        self.config = config or RetrievalConfig()

    def route(self, tool_name: str) -> CollectionDecision:
        if tool_name in self.SPECIAL:
            return CollectionDecision(
                self.config.special_collection, f"specialized_{tool_name}_corpus", None
            )
        return CollectionDecision(
            self.config.generic_collection,
            "generic_medical_ab_selected_v1",
            self.config.special_collection,
        )
