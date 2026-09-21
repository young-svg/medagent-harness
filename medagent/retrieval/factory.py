from __future__ import annotations

from importlib.util import find_spec
from typing import TYPE_CHECKING

from medagent.retrieval.backend import (
    FakeRetrievalBackend,
    MilvusRetrievalBackend,
    OffRetrievalBackend,
    RetrievalBackend,
)
from medagent.retrieval.embedding import SentenceTransformerEmbedder

if TYPE_CHECKING:
    from medagent.runtime.config import RuntimeConfig


class RetrievalConfigurationError(RuntimeError):
    """The selected retrieval mode is incomplete or unavailable."""


def build_retrieval_backend(config: RuntimeConfig) -> RetrievalBackend:
    if config.retrieval_mode == "off":
        return OffRetrievalBackend()
    if config.retrieval_mode == "fake":
        return FakeRetrievalBackend()
    if config.retrieval_mode != "milvus":
        raise RetrievalConfigurationError(f"unsupported retrieval mode: {config.retrieval_mode}")
    if not config.milvus_uri.strip():
        raise RetrievalConfigurationError(
            "MEDAGENT_MILVUS_URI is required when MEDAGENT_RETRIEVAL_MODE=milvus"
        )
    if not config.embedding_model.strip():
        raise RetrievalConfigurationError(
            "MEDAGENT_EMBEDDING_MODEL is required when MEDAGENT_RETRIEVAL_MODE=milvus"
        )
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
    try:
        embedder = SentenceTransformerEmbedder(config.embedding_model)
        return MilvusRetrievalBackend(
            uri=config.milvus_uri,
            token=config.milvus_token,
            embed_query=embedder.embed,
        )
    except Exception as error:
        raise RetrievalConfigurationError(
            f"Unable to initialize Milvus retrieval: {type(error).__name__}: {error}"
        ) from error
