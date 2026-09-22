"""Clinical retrieval and evidence admission."""

from medagent.retrieval.backend import (
    FakeRetrievalBackend,
    MilvusRetrievalBackend,
    OffRetrievalBackend,
    RetrievalBackend,
    RetrievalSchemaError,
)
from medagent.retrieval.config import ExternalMedicalKnowledgeConfig
from medagent.retrieval.embedding import EmbeddingProvider, SentenceTransformerEmbedder
from medagent.retrieval.factory import (
    RetrievalConfigurationError,
    build_external_medical_knowledge_backend,
    build_retrieval_backend,
)

__all__ = [
    "EmbeddingProvider",
    "ExternalMedicalKnowledgeConfig",
    "FakeRetrievalBackend",
    "MilvusRetrievalBackend",
    "OffRetrievalBackend",
    "RetrievalBackend",
    "RetrievalConfigurationError",
    "RetrievalSchemaError",
    "SentenceTransformerEmbedder",
    "build_external_medical_knowledge_backend",
    "build_retrieval_backend",
]
