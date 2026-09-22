from __future__ import annotations

from importlib.util import find_spec
from typing import TYPE_CHECKING

from medagent.retrieval.backend import (
    FakeRetrievalBackend,
    MilvusRetrievalBackend,
    OffRetrievalBackend,
    RetrievalBackend,
)
from medagent.retrieval.config import ExternalMedicalKnowledgeConfig
from medagent.retrieval.embedding import SentenceTransformerEmbedder

if TYPE_CHECKING:
    from medagent.runtime.config import RuntimeConfig


class RetrievalConfigurationError(RuntimeError):
    """The selected retrieval mode is incomplete or unavailable."""


def _require_retrieval_packages() -> None:
    missing = [
        package
        for package in ("pymilvus", "sentence_transformers")
        if find_spec(package) is None
    ]
    if missing:
        names = ", ".join(missing)
        raise RetrievalConfigurationError(
            f"Milvus retrieval requires optional packages: {names}. "
            'Install them with pip install -e ".[retrieval]".'
        )


def build_external_medical_knowledge_backend(
    external: ExternalMedicalKnowledgeConfig,
    *,
    logical_general: str = "clinical_knowledge",
    logical_special: str = "clinical_guidelines",
    token: str = "",
) -> MilvusRetrievalBackend:
    """Build the strict read-only adapter used by Native and the real smoke script."""
    try:
        external.validate()
    except (TypeError, ValueError) as error:
        raise RetrievalConfigurationError(str(error)) from error
    if not external.enabled:
        raise RetrievalConfigurationError("external medical knowledge is not enabled")
    _require_retrieval_packages()
    try:
        embedder = SentenceTransformerEmbedder(external.embedding_model)
        return MilvusRetrievalBackend(
            uri=external.path,
            token=token,
            embed_query=embedder.embed,
            collection_mapping=external.collection_mapping(
                logical_general=logical_general,
                logical_special=logical_special,
            ),
            expected_dimension=external.expected_dimension,
            expected_metric=external.expected_metric,
            require_content_metadata=True,
            strict_metadata=True,
        )
    except Exception as error:
        raise RetrievalConfigurationError(
            f"Unable to initialize external MedicalQA retrieval: "
            f"{type(error).__name__}: {error}"
        ) from error


def build_retrieval_backend(config: RuntimeConfig) -> RetrievalBackend:
    try:
        external = ExternalMedicalKnowledgeConfig.from_env()
        external.validate()
    except (TypeError, ValueError) as error:
        raise RetrievalConfigurationError(str(error)) from error
    if external.enabled and config.retrieval_mode != "milvus":
        raise RetrievalConfigurationError(
            "MEDICAL_KB_ENABLED=true requires MEDAGENT_RETRIEVAL_MODE=milvus"
        )
    if config.retrieval_mode == "off":
        return OffRetrievalBackend()
    if config.retrieval_mode == "fake":
        return FakeRetrievalBackend()
    if config.retrieval_mode != "milvus":
        raise RetrievalConfigurationError(f"unsupported retrieval mode: {config.retrieval_mode}")
    if external.enabled:
        return build_external_medical_knowledge_backend(
            external,
            logical_general=config.generic_collection,
            logical_special=config.special_collection,
            token=config.milvus_token,
        )
    milvus_uri = config.milvus_uri
    embedding_model = config.embedding_model
    if not milvus_uri.strip():
        raise RetrievalConfigurationError(
            "MEDAGENT_MILVUS_URI is required when MEDAGENT_RETRIEVAL_MODE=milvus"
        )
    if not embedding_model.strip():
        raise RetrievalConfigurationError(
            "MEDAGENT_EMBEDDING_MODEL is required when MEDAGENT_RETRIEVAL_MODE=milvus"
        )
    _require_retrieval_packages()
    try:
        embedder = SentenceTransformerEmbedder(embedding_model)
        return MilvusRetrievalBackend(
            uri=milvus_uri,
            token=config.milvus_token,
            embed_query=embedder.embed,
        )
    except Exception as error:
        raise RetrievalConfigurationError(
            f"Unable to initialize Milvus retrieval: {type(error).__name__}: {error}"
        ) from error
