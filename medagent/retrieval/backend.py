from __future__ import annotations

import asyncio
import inspect
import json
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
            "content",
            "metadata",
        ]
        self.resolved_vector_fields: dict[str, str] = {}

    async def _prepare_collection(self, collection: str) -> str:
        description = await asyncio.to_thread(
            self._client.describe_collection, collection_name=collection
        )
        fields = list(description.get("fields") or [])
        names = {str(field.get("name")) for field in fields}
        vector_field = self.vector_field
        if vector_field not in names:
            candidates = [
                str(field.get("name"))
                for field in fields
                if "VECTOR" in str(field.get("type") or field.get("dtype") or "").upper()
                or "dim" in (field.get("params") or {})
            ]
            if len(candidates) != 1:
                raise RuntimeError(
                    f"configured vector field {vector_field!r} is absent from {collection!r} "
                    f"and schema has {len(candidates)} vector candidates"
                )
            vector_field = candidates[0]
        await asyncio.to_thread(self._client.load_collection, collection_name=collection)
        self.resolved_vector_fields[collection] = vector_field
        return vector_field

    async def search(self, query: str, collection: str, top_k: int) -> list[EvidenceItem]:
        vector = self.embed_query(query)
        if inspect.isawaitable(vector):
            vector = await vector
        vector_field = await self._prepare_collection(collection)
        rows = await asyncio.to_thread(
            self._client.search,
            collection_name=collection,
            data=[vector],
            anns_field=vector_field,
            limit=top_k,
            output_fields=self.output_fields,
        )
        hits = rows[0] if rows else []
        items = []
        for rank, hit in enumerate(hits, 1):
            entity = dict(hit.get("entity") or {})
            raw_metadata = entity.get("metadata")
            if isinstance(raw_metadata, str):
                try:
                    metadata = dict(json.loads(raw_metadata))
                except (TypeError, ValueError, json.JSONDecodeError):
                    metadata = {"raw_metadata": raw_metadata}
            elif isinstance(raw_metadata, dict):
                metadata = dict(raw_metadata)
            else:
                metadata = {}
            raw_id = (
                entity.get("document_id")
                or metadata.get("doc_id")
                or hit.get("id", f"row-{rank}")
            )
            metadata.update(
                {
                    key: value
                    for key, value in entity.items()
                    if key
                    not in {
                        "document_id",
                        "source_id",
                        "title",
                        "section",
                        "source",
                        "url",
                        "text",
                        "content",
                        "metadata",
                        self.vector_field,
                    }
                }
            )
            items.append(
                EvidenceItem(
                    evidence_id=f"milvus-{raw_id}-{rank}",
                    document_id=raw_id,
                    source_id=entity.get("source_id") or metadata.get("source_record_id"),
                    title=entity.get("title") or metadata.get("disease") or metadata.get("topic"),
                    section=entity.get("section") or metadata.get("type") or metadata.get("topic"),
                    source=entity.get("source") or metadata.get("source"),
                    url=entity.get("url") or metadata.get("url"),
                    text=str(
                        entity.get("text")
                        or entity.get("content")
                        or entity.get("page_content")
                        or ""
                    ),
                    score=float(hit.get("distance", hit.get("score", 0.0))),
                    rank=rank,
                    metadata=metadata,
                )
            )
        return items

    async def close(self) -> None:
        await asyncio.to_thread(self._client.close)
