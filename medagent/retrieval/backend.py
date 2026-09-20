from __future__ import annotations

from typing import Protocol

from medagent.retrieval.evidence import EvidenceItem


class RetrievalBackend(Protocol):
    def search(self, query: str, collection: str, top_k: int) -> list[EvidenceItem]: ...


class NullRetrievalBackend:
    """Offline-safe backend used unless an external adapter is explicitly configured."""

    def search(self, query: str, collection: str, top_k: int) -> list[EvidenceItem]:
        return []
