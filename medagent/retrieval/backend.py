from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Callable, Iterable
from typing import Any, Protocol

from medagent.retrieval.evidence import EvidenceItem


class RetrievalSchemaError(RuntimeError):
    """The configured collection cannot satisfy the Native evidence contract."""


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
        collection_mapping: dict[str, str] | None = None,
        expected_dimension: int | None = None,
        expected_metric: str | None = None,
        require_content_metadata: bool = False,
        strict_metadata: bool = False,
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
        self.collection_mapping = dict(collection_mapping or {})
        self.expected_dimension = expected_dimension
        self.expected_metric = expected_metric.upper() if expected_metric else None
        self.require_content_metadata = require_content_metadata
        self.strict_metadata = strict_metadata
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
        self.resolved_collections: dict[str, str] = {}
        self.schema_validation: dict[str, dict[str, Any]] = {}

    def _physical_collection(self, collection: str) -> str:
        mapping = getattr(self, "collection_mapping", {})
        return mapping.get(collection, collection)

    @staticmethod
    def _vector_candidates(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            field
            for field in fields
            if "VECTOR" in str(field.get("type") or field.get("dtype") or "").upper()
            or "dim" in (field.get("params") or {})
            or field.get("dim") is not None
        ]

    async def validate_collection(self, collection: str) -> dict[str, Any]:
        if not hasattr(self, "schema_validation"):
            self.schema_validation = {}
        if not hasattr(self, "resolved_collections"):
            self.resolved_collections = {}
        cached = getattr(self, "schema_validation", {}).get(collection)
        if cached is not None:
            return dict(cached)

        physical_collection = self._physical_collection(collection)
        if hasattr(self._client, "has_collection"):
            exists = await asyncio.to_thread(
                self._client.has_collection, collection_name=physical_collection
            )
            if not exists:
                raise RetrievalSchemaError(
                    f"mapped Milvus collection does not exist: logical={collection!r}, "
                    f"physical={physical_collection!r}"
                )

        try:
            description = await asyncio.to_thread(
                self._client.describe_collection, collection_name=physical_collection
            )
        except Exception as error:
            raise RetrievalSchemaError(
                f"unable to describe Milvus collection {physical_collection!r}: "
                f"{type(error).__name__}: {error}"
            ) from error

        fields = list(description.get("fields") or [])
        field_by_name = {str(field.get("name")): field for field in fields}
        vector_field = getattr(self, "vector_field", "embedding")
        vector_spec = field_by_name.get(vector_field)
        if vector_spec is None:
            candidates = self._vector_candidates(fields)
            if len(candidates) != 1:
                raise RetrievalSchemaError(
                    f"configured vector field {vector_field!r} is absent from "
                    f"{physical_collection!r} and schema has {len(candidates)} vector candidates"
                )
            vector_spec = candidates[0]
            vector_field = str(vector_spec.get("name"))

        params = dict(vector_spec.get("params") or {})
        raw_dimension = params.get("dim", vector_spec.get("dim"))
        dimension = int(raw_dimension) if raw_dimension is not None else None
        expected_dimension = getattr(self, "expected_dimension", None)
        if expected_dimension is not None and dimension != expected_dimension:
            raise RetrievalSchemaError(
                f"collection {physical_collection!r} vector dimension is {dimension!r}; "
                f"expected {expected_dimension}"
            )

        metric = None
        index_name = None
        expected_metric = getattr(self, "expected_metric", None)
        if expected_metric is not None:
            try:
                indexes = await asyncio.to_thread(
                    self._client.list_indexes, collection_name=physical_collection
                )
                matching_index = None
                for candidate_name in indexes:
                    index = await asyncio.to_thread(
                        self._client.describe_index,
                        collection_name=physical_collection,
                        index_name=candidate_name,
                    )
                    if str(index.get("field_name")) == vector_field:
                        matching_index = index
                        index_name = str(candidate_name)
                        break
                if matching_index is None:
                    raise RetrievalSchemaError(
                        f"collection {physical_collection!r} has no index for {vector_field!r}"
                    )
                metric = str(matching_index.get("metric_type") or "").upper()
            except RetrievalSchemaError:
                raise
            except Exception as error:
                raise RetrievalSchemaError(
                    f"unable to inspect vector index for {physical_collection!r}: "
                    f"{type(error).__name__}: {error}"
                ) from error
            if metric != expected_metric:
                raise RetrievalSchemaError(
                    f"collection {physical_collection!r} metric is {metric!r}; "
                    f"expected {expected_metric!r}"
                )

        await asyncio.to_thread(
            self._client.load_collection, collection_name=physical_collection
        )

        content_readable = None
        metadata_parseable = None
        if getattr(self, "require_content_metadata", False):
            try:
                rows = await asyncio.to_thread(
                    self._client.query,
                    collection_name=physical_collection,
                    filter="",
                    output_fields=["content", "metadata"],
                    limit=1,
                )
            except Exception as error:
                raise RetrievalSchemaError(
                    f"unable to read content/metadata from {physical_collection!r}: "
                    f"{type(error).__name__}: {error}"
                ) from error
            if not rows:
                raise RetrievalSchemaError(
                    f"collection {physical_collection!r} is empty; "
                    "content/metadata cannot be verified"
                )
            sample = dict(rows[0])
            content_readable = isinstance(sample.get("content"), str) and bool(
                sample["content"].strip()
            )
            raw_metadata = sample.get("metadata")
            if isinstance(raw_metadata, str):
                try:
                    parsed_metadata = json.loads(raw_metadata)
                except json.JSONDecodeError as error:
                    raise RetrievalSchemaError(
                        f"collection {physical_collection!r} metadata is not valid JSON: {error}"
                    ) from error
                metadata_parseable = isinstance(parsed_metadata, dict)
            else:
                metadata_parseable = isinstance(raw_metadata, dict)
            if not content_readable:
                raise RetrievalSchemaError(
                    f"collection {physical_collection!r} has no readable content field"
                )
            if not metadata_parseable:
                raise RetrievalSchemaError(
                    f"collection {physical_collection!r} metadata is not a JSON object"
                )

        result = {
            "logical_collection": collection,
            "physical_collection": physical_collection,
            "vector_field": vector_field,
            "dimension": dimension,
            "metric": metric,
            "index_name": index_name,
            "dynamic_fields": bool(description.get("enable_dynamic_field")),
            "content_readable": content_readable,
            "metadata_parseable": metadata_parseable,
            "access_mode": "read_only",
        }
        self.resolved_vector_fields[collection] = vector_field
        self.resolved_collections[collection] = physical_collection
        self.schema_validation[collection] = result
        return dict(result)

    async def _prepare_collection(self, collection: str) -> tuple[str, str]:
        validation = await self.validate_collection(collection)
        return str(validation["physical_collection"]), str(validation["vector_field"])

    async def search(self, query: str, collection: str, top_k: int) -> list[EvidenceItem]:
        vector = self.embed_query(query)
        if inspect.isawaitable(vector):
            vector = await vector
        physical_collection, vector_field = await self._prepare_collection(collection)
        expected_dimension = getattr(self, "expected_dimension", None)
        if expected_dimension is not None and len(vector) != expected_dimension:
            raise RetrievalSchemaError(
                f"query embedding dimension is {len(vector)}; expected {expected_dimension}"
            )
        rows = await asyncio.to_thread(
            self._client.search,
            collection_name=physical_collection,
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
                except (TypeError, ValueError, json.JSONDecodeError) as error:
                    if getattr(self, "strict_metadata", False):
                        raise RetrievalSchemaError(
                            f"search result from {physical_collection!r} has invalid metadata JSON"
                        ) from error
                    metadata = {"raw_metadata": raw_metadata}
            elif isinstance(raw_metadata, dict):
                metadata = dict(raw_metadata)
            else:
                if getattr(self, "strict_metadata", False):
                    raise RetrievalSchemaError(
                        f"search result from {physical_collection!r} has no metadata object"
                    )
                metadata = {}
            raw_id = (
                entity.get("document_id")
                or metadata.get("doc_id")
                or hit.get("id", f"row-{rank}")
            )
            stable_evidence_id = (
                entity.get("evidence_id")
                or metadata.get("evidence_id")
                or hit.get("id")
                or metadata.get("id")
                or f"{raw_id}:{metadata.get('chunk_id', 'document')}"
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
            text = str(
                entity.get("text")
                or entity.get("content")
                or entity.get("page_content")
                or ""
            )
            if getattr(self, "require_content_metadata", False) and not text.strip():
                raise RetrievalSchemaError(
                    f"search result from {physical_collection!r} has no readable content"
                )
            items.append(
                EvidenceItem(
                    evidence_id=f"milvus-{stable_evidence_id}",
                    document_id=raw_id,
                    source_id=entity.get("source_id") or metadata.get("source_record_id"),
                    title=entity.get("title") or metadata.get("disease") or metadata.get("topic"),
                    section=entity.get("section") or metadata.get("type") or metadata.get("topic"),
                    source=entity.get("source") or metadata.get("source"),
                    url=entity.get("url") or metadata.get("url"),
                    text=text,
                    score=float(hit.get("distance", hit.get("score", 0.0))),
                    rank=rank,
                    metadata=metadata,
                )
            )
        return items

    async def close(self) -> None:
        await asyncio.to_thread(self._client.close)
