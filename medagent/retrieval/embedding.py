from __future__ import annotations

from typing import Protocol


class EmbeddingProvider(Protocol):
    def embed(self, text: str) -> list[float]: ...


class SentenceTransformerEmbedder:
    """Optional public embedder configured by model name, never by a local model path."""

    def __init__(self, model_name: str) -> None:
        if not model_name.strip():
            raise ValueError("MEDAGENT_EMBEDDING_MODEL is required for Milvus retrieval")
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as error:
            raise RuntimeError(
                "Install the optional 'retrieval' dependency to use sentence-transformers"
            ) from error
        self.model_name = model_name
        try:
            self._model = SentenceTransformer(model_name)
        except Exception as error:
            raise RuntimeError(
                f"Unable to load embedding model {model_name!r}: "
                f"{type(error).__name__}: {error}"
            ) from error

    def embed(self, text: str) -> list[float]:
        vector = self._model.encode(text, normalize_embeddings=True)
        return [float(value) for value in vector]
