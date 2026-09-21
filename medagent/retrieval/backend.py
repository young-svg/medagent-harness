from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable, Iterable
from typing import Any, Protocol

from medagent.retrieval.evidence import EvidenceItem


class RetrievalBackend(Protocol):
    async def search(self, query: str, collection: str, top_k: int) -> list[EvidenceItem]: ...


class OffRetrievalBackend:
    async def search(self, query: str, collection: str, top_k: int) -> list[EvidenceItem]:
        raise RuntimeError("clinical retrieval is disabled by MEDAGENT_RETRIEVAL_MODE=off")


class FakeRetrievalBackend:
    def __init__(self, items: Iterable[EvidenceItem] = ()) -> None:
        self.items = list(items)
        self.requests: list[dict[str, Any]] = []

    async def search(self, query: str, collection: str, top_k: int) -> list[EvidenceItem]:
        self.requests.append({"query": query, "collection": collection, "top_k": top_k})
        return self.items[:top_k]


class MilvusRetrievalBackend:
    """Optional Milvus adapter with an application-supplied query embedder."""

    def __init__(
        self,
        *,
        uri: str,
        embed_query: Callable[[str], list[float]],
        token: str = "",
        vector_field: str = "embedding",
        output_fields: list[str] | None = None,
    ) -> None:
        try:
            from pymilvus import MilvusClient
        except ImportError as error:
            raise RuntimeError(
                "Install the optional 'retrieval' dependency to use the Milvus backend"
            ) from error
        self._client = MilvusClient(uri=uri, token=token or None)
        self.embed_query = embed_query
        self.vector_field = vector_field
        self.output_fields = output_fields or [
            "document_id",
            "source_id",
            "title",
            "section",
            "source",
            "url",
            "text",
        ]

    async def search(self, query: str, collection: str, top_k: int) -> list[EvidenceItem]:
        vector = self.embed_query(query)
        if inspect.isawaitable(vector):
            vector = await vector
        rows = await asyncio.to_thread(
            self._client.search,
            collection_name=collection,
            data=[vector],
            anns_field=self.vector_field,
            limit=top_k,
            output_fields=self.output_fields,
        )
        hits = rows[0] if rows else []
        items = []
        for rank, hit in enumerate(hits, 1):
            entity = dict(hit.get("entity") or {})
            raw_id = entity.get("document_id", hit.get("id", f"row-{rank}"))
            items.append(
                EvidenceItem(
                    evidence_id=f"milvus-{raw_id}-{rank}",
                    document_id=raw_id,
                    source_id=entity.get("source_id"),
                    title=entity.get("title"),
                    section=entity.get("section"),
                    source=entity.get("source"),
                    url=entity.get("url"),
                    text=str(entity.get("text") or ""),
                    score=float(hit.get("distance", hit.get("score", 0.0))),
                    rank=rank,
                    metadata={
                        key: value
                        for key, value in entity.items()
                        if key not in self.output_fields
                    },
                )
            )
        return items
