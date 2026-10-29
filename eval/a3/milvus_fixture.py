"""Build and verify an isolated Milvus Lite store from the four frozen A3 texts."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from importlib.metadata import version
from pathlib import Path
from typing import Any

from eval.a3.runner import load_frozen_cases, outside_repository
from medagent.retrieval.admission import admit_evidence
from medagent.retrieval.backend import MilvusRetrievalBackend
from medagent.retrieval.collection_router import CollectionRouter


def _cached_model(name: str) -> Any:
    # Offline flags must precede the import; local_files_only is the final guard.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(name, local_files_only=True)


def _check_fixture(fixture: dict[str, Any]) -> None:
    if (
        fixture["embedding_model"] != "BAAI/bge-small-zh-v1.5"
        or fixture["normalize_embeddings"] is not True
        or fixture["dimension"] != 512
        or fixture["metric"] != "COSINE"
        or fixture["threshold"] != 0.63
        or fixture["collections"] != ["clinical_guidelines", "clinical_knowledge"]
        or [document["id"] for document in fixture["documents"]]
        != ["SYN-R1", "SYN-R2", "SYN-R3A", "SYN-R3B"]
    ):
        raise ValueError("A3 Milvus fixture differs from the frozen protocol")


def create_database(path: Path) -> dict[str, Any]:
    """Only a new external path is accepted; no existing DB can be dropped."""
    payload, _ = load_frozen_cases()
    fixture = payload["rag_fixture"]
    _check_fixture(fixture)
    path = outside_repository(path)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite existing Milvus database: {path}")
    if not path.parent.is_dir():
        raise ValueError("Milvus database parent directory must already exist")

    model = _cached_model(fixture["embedding_model"])
    vectors = model.encode(
        [document["text"] for document in fixture["documents"]],
        normalize_embeddings=True, show_progress_bar=False,
    )
    if vectors.shape != (4, 512):
        raise ValueError(f"cached embedding returned wrong shape: {vectors.shape}")
    norms = [sum(float(value) ** 2 for value in vector) ** 0.5 for vector in vectors]
    if any(abs(norm - 1.0) > 0.01 for norm in norms):
        raise ValueError("cached embedding did not produce normalized vectors")

    from pymilvus import MilvusClient

    client = MilvusClient(uri=str(path))
    try:
        for collection in fixture["collections"]:
            client.create_collection(
                collection_name=collection, dimension=512,
                primary_field_name="id", id_type="string", max_length=256,
                vector_field_name="embedding", metric_type="COSINE",
                consistency_level="Strong",
            )
            # Duplicate the same four frozen texts across the two logical routes;
            # no new evidence is invented and both tool families can find each ID.
            rows = [
                {
                    "id": document["id"],
                    "embedding": [float(value) for value in vector],
                    "document_id": document["id"],
                    "source_id": document["id"],
                    "title": document["title"],
                    "section": "A3 synthetic fixture",
                    "source": document["source"],
                    "url": "https://example.invalid/a3-synthetic-only",
                    "text": document["text"],
                    "content": document["text"],
                    "metadata": json.dumps(
                        {"content_kind": "attributed_evidence_summary",
                         "embedding_model": fixture["embedding_model"],
                         "normalize_embeddings": True, "synthetic": True},
                        ensure_ascii=False,
                    ),
                }
                for document, vector in zip(fixture["documents"], vectors, strict=True)
            ]
            client.insert(collection_name=collection, data=rows)
            client.flush(collection_name=collection)
    finally:
        client.close()
    return {"database": str(path), "embedding_model": fixture["embedding_model"],
            "sentence_transformers_version": version("sentence-transformers"),
            "dimension": 512, "metric": "COSINE", "normalized": True,
            "collections": fixture["collections"], "source_documents": 4,
            "rows_per_collection": 4}


async def open_verified_backend(
    path: Path, fixture: dict[str, Any]
) -> tuple[MilvusRetrievalBackend, dict[str, Any]]:
    _check_fixture(fixture)
    path = outside_repository(path)
    # pymilvus 3.x Milvus Lite may store its local database as a directory.
    if not path.exists() or not (path.is_file() or path.is_dir()):
        raise FileNotFoundError(path)
    model = _cached_model(fixture["embedding_model"])

    def embed_query(text: str) -> list[float]:
        vector = model.encode(text, normalize_embeddings=True)
        if len(vector) != 512:
            raise ValueError("query embedding dimension changed")
        return [float(value) for value in vector]

    backend = MilvusRetrievalBackend(
        uri=str(path), embed_query=embed_query,
        collection_mapping={collection: collection for collection in fixture["collections"]},
        expected_dimension=512, expected_metric="COSINE",
        require_content_metadata=True, strict_metadata=True,
    )
    try:
        schemas = {
            collection: await backend.validate_collection(collection)
            for collection in fixture["collections"]
        }
        for collection in fixture["collections"]:
            rows = await asyncio.to_thread(
                backend._client.query, collection_name=collection,
                filter="", output_fields=["document_id", "metadata", "content"], limit=10,
            )
            if {row.get("document_id") for row in rows} != {
                "SYN-R1", "SYN-R2", "SYN-R3A", "SYN-R3B"
            } or len(rows) != 4:
                raise ValueError(
                    f"A3 collection {collection} does not contain exactly the frozen IDs"
                )
            for row in rows:
                expected = next(
                    item for item in fixture["documents"] if item["id"] == row["document_id"]
                )
                metadata = json.loads(row["metadata"])
                if (
                    row["content"] != expected["text"]
                    or metadata.get("embedding_model") != fixture["embedding_model"]
                    or metadata.get("normalize_embeddings") is not True
                ):
                    raise ValueError(f"A3 collection {collection} has mismatched frozen content")
        hits = await backend.search(
            "合成随访卡 QF-12 黄色状态第3天核对", "clinical_knowledge", 4
        )
        if not hits or not any(item.document_id == "SYN-R2" for item in hits):
            raise ValueError("A3 Milvus query did not retrieve the frozen QF-12 document")
        admit_evidence(hits, threshold=fixture["threshold"])
        if not any(item.document_id == "SYN-R2" and item.admitted for item in hits):
            raise ValueError("A3 QF-12 evidence was not admitted at the frozen threshold")
        router = CollectionRouter()
        if (router.route("clinical_guideline").collection != "clinical_guidelines"
                or router.route("search_knowledge").collection != "clinical_knowledge"):
            raise ValueError("A3 logical Collection routing has changed")
        return backend, {
            "schemas": schemas,
            "probe_query": "合成随访卡 QF-12 黄色状态第3天核对",
            "probe_hits": [
                {"id": item.document_id, "score": item.score,
                 "admitted": item.admitted, "reason": item.admission_reason}
                for item in hits
            ],
            "embedding_model": fixture["embedding_model"],
            "sentence_transformers_version": version("sentence-transformers"),
            "normalized": True,
        }
    except Exception:
        backend._client.close()
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Build an offline A3 synthetic Milvus Lite DB")
    parser.add_argument("--db", type=Path, required=True, help="new path outside the repository")
    args = parser.parse_args()
    metadata = create_database(args.db)
    payload, _ = load_frozen_cases()
    backend, verification = asyncio.run(open_verified_backend(args.db, payload["rag_fixture"]))
    backend._client.close()
    print(json.dumps({**metadata, "verification": verification}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
