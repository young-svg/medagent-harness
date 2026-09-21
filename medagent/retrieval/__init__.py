"""Clinical retrieval and evidence admission."""

from medagent.retrieval.backend import (
    FakeRetrievalBackend,
    MilvusRetrievalBackend,
    OffRetrievalBackend,
    RetrievalBackend,
)
from medagent.retrieval.embedding import EmbeddingProvider, SentenceTransformerEmbedder
from medagent.retrieval.factory import RetrievalConfigurationError, build_retrieval_backend

__all__ = [
    "EmbeddingProvider",
    "FakeRetrievalBackend",
    "MilvusRetrievalBackend",
    "OffRetrievalBackend",
    "RetrievalBackend",
    "RetrievalConfigurationError",
    "SentenceTransformerEmbedder",
    "build_retrieval_backend",
]
