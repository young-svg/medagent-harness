from __future__ import annotations

import json
from pathlib import Path

import pytest

from medagent.retrieval.backend import MilvusRetrievalBackend, RetrievalSchemaError
from medagent.retrieval.config import ExternalMedicalKnowledgeConfig
from medagent.retrieval.factory import RetrievalConfigurationError, build_retrieval_backend
from medagent.runtime.config import RuntimeConfig


def test_external_medical_kb_is_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "MEDICAL_KB_ENABLED",
        "MEDICAL_KB_PATH",
        "MEDICAL_KB_GENERAL_COLLECTION",
        "MEDICAL_KB_SPECIAL_COLLECTION",
    ):
        monkeypatch.delenv(name, raising=False)

    config = ExternalMedicalKnowledgeConfig.from_env()

    assert config.enabled is False
    assert config.path == ""
    assert config.general_collection == "medical_knowledge_v1"
    assert config.special_collection == "medical_knowledge"


def test_external_medical_kb_env_mapping(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    database = tmp_path / "medical.db"
    database.mkdir()
    monkeypatch.setenv("MEDICAL_KB_ENABLED", "true")
    monkeypatch.setenv("MEDICAL_KB_PATH", str(database))
    monkeypatch.setenv("MEDICAL_KB_GENERAL_COLLECTION", "general_v1")
    monkeypatch.setenv("MEDICAL_KB_SPECIAL_COLLECTION", "special_v1")

    config = ExternalMedicalKnowledgeConfig.from_env()
    config.validate()

    assert config.collection_mapping(
        logical_general="clinical_knowledge", logical_special="clinical_guidelines"
    ) == {
        "clinical_knowledge": "general_v1",
        "clinical_guidelines": "special_v1",
        "general_v1": "general_v1",
        "special_v1": "special_v1",
    }


def test_native_factory_requires_milvus_mode_for_external_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    database = tmp_path / "medical.db"
    database.mkdir()
    monkeypatch.setenv("MEDICAL_KB_ENABLED", "true")
    monkeypatch.setenv("MEDICAL_KB_PATH", str(database))

    with pytest.raises(RetrievalConfigurationError, match="requires MEDAGENT_RETRIEVAL_MODE"):
        build_retrieval_backend(RuntimeConfig(retrieval_mode="off"))


def test_native_factory_uses_external_builder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    database = tmp_path / "medical.db"
    database.mkdir()
    sentinel = object()
    captured: dict[str, object] = {}
    monkeypatch.setenv("MEDICAL_KB_ENABLED", "true")
    monkeypatch.setenv("MEDICAL_KB_PATH", str(database))

    def fake_builder(
        external: ExternalMedicalKnowledgeConfig, **kwargs: object
    ) -> object:
        captured["external"] = external
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(
        "medagent.retrieval.factory.build_external_medical_knowledge_backend",
        fake_builder,
    )
    result = build_retrieval_backend(
        RuntimeConfig(
            retrieval_mode="milvus",
            generic_collection="logical_general",
            special_collection="logical_special",
        )
    )

    assert result is sentinel
    assert captured["logical_general"] == "logical_general"
    assert captured["logical_special"] == "logical_special"
    assert isinstance(captured["external"], ExternalMedicalKnowledgeConfig)


@pytest.mark.asyncio
async def test_external_backend_validates_schema_maps_collection_and_preserves_evidence() -> None:
    class StubClient:
        def __init__(self) -> None:
            self.loaded: list[str] = []
            self.searched_collection = ""

        def has_collection(self, *, collection_name: str) -> bool:
            return collection_name == "medical_knowledge_v1"

        def describe_collection(self, *, collection_name: str) -> dict[str, object]:
            assert collection_name == "medical_knowledge_v1"
            return {
                "enable_dynamic_field": True,
                "fields": [
                    {"name": "id", "type": 5, "params": {}},
                    {"name": "vector", "type": 101, "params": {"dim": 512}},
                ],
            }

        def list_indexes(self, *, collection_name: str) -> list[str]:
            assert collection_name == "medical_knowledge_v1"
            return ["vector"]

        def describe_index(
            self, *, collection_name: str, index_name: str
        ) -> dict[str, str]:
            assert collection_name == "medical_knowledge_v1"
            assert index_name == "vector"
            return {
                "field_name": "vector",
                "metric_type": "COSINE",
            }

        def load_collection(self, *, collection_name: str) -> None:
            self.loaded.append(collection_name)

        def query(self, **kwargs: object) -> list[dict[str, object]]:
            assert kwargs["collection_name"] == "medical_knowledge_v1"
            return [
                {
                    "content": "医学证据正文",
                    "metadata": json.dumps(
                        {"doc_id": "doc-1", "type": "treatment", "source": "MedicalQA-DX"}
                    ),
                }
            ]

        def search(self, **kwargs: object) -> list[list[dict[str, object]]]:
            self.searched_collection = str(kwargs["collection_name"])
            return [
                [
                    {
                        "id": 17,
                        "distance": 0.88,
                        "entity": {
                            "content": "医学证据正文",
                            "metadata": json.dumps(
                                {
                                    "doc_id": "doc-1",
                                    "type": "treatment",
                                    "source": "MedicalQA-DX",
                                }
                            ),
                        },
                    }
                ]
            ]

        def close(self) -> None:
            return None

    backend = object.__new__(MilvusRetrievalBackend)
    backend._client = StubClient()
    backend.embed_query = lambda text: [0.1] * 512
    backend.vector_field = "embedding"
    backend.output_fields = ["content", "metadata"]
    backend.collection_mapping = {"clinical_knowledge": "medical_knowledge_v1"}
    backend.expected_dimension = 512
    backend.expected_metric = "COSINE"
    backend.require_content_metadata = True
    backend.strict_metadata = True
    backend.resolved_vector_fields = {}
    backend.resolved_collections = {}
    backend.schema_validation = {}

    validation = await backend.validate_collection("clinical_knowledge")
    items = await backend.search("糖尿病治疗原则", "clinical_knowledge", 5)

    assert validation == {
        "logical_collection": "clinical_knowledge",
        "physical_collection": "medical_knowledge_v1",
        "vector_field": "vector",
        "dimension": 512,
        "metric": "COSINE",
        "index_name": "vector",
        "dynamic_fields": True,
        "content_readable": True,
        "metadata_parseable": True,
        "access_mode": "read_only",
    }
    assert backend._client.searched_collection == "medical_knowledge_v1"
    assert items[0].document_id == "doc-1"
    assert items[0].text == "医学证据正文"
    assert items[0].source == "MedicalQA-DX"
    assert items[0].section == "treatment"
    assert items[0].url is None


@pytest.mark.asyncio
async def test_external_backend_rejects_schema_dimension_mismatch() -> None:
    class StubClient:
        def has_collection(self, *, collection_name: str) -> bool:
            return True

        def describe_collection(self, *, collection_name: str) -> dict[str, object]:
            return {
                "fields": [
                    {"name": "vector", "type": 101, "params": {"dim": 384}},
                ]
            }

    backend = object.__new__(MilvusRetrievalBackend)
    backend._client = StubClient()
    backend.vector_field = "vector"
    backend.collection_mapping = {}
    backend.expected_dimension = 512
    backend.expected_metric = "COSINE"
    backend.require_content_metadata = True
    backend.schema_validation = {}
    backend.resolved_collections = {}

    with pytest.raises(RetrievalSchemaError, match="dimension is 384"):
        await backend.validate_collection("medical_knowledge_v1")
