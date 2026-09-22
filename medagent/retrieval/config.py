from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError(f"{name} must be a boolean flag")


@dataclass(frozen=True, slots=True)
class RetrievalConfig:
    generic_collection: str = "clinical_knowledge"
    special_collection: str = "clinical_guidelines"
    top_k: int = 5
    admission_threshold: float = 0.63
    max_admitted_evidence: int = 5
    max_evidence_chars: int = 1200


@dataclass(frozen=True, slots=True)
class ExternalMedicalKnowledgeConfig:
    """Opt-in configuration for a local, externally managed medical corpus."""

    enabled: bool = False
    path: str = ""
    general_collection: str = "medical_knowledge_v1"
    special_collection: str = "medical_knowledge"
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    expected_dimension: int = 512
    expected_metric: str = "COSINE"

    @classmethod
    def from_env(cls) -> ExternalMedicalKnowledgeConfig:
        return cls(
            enabled=_env_flag("MEDICAL_KB_ENABLED"),
            path=os.getenv("MEDICAL_KB_PATH", "").strip(),
            general_collection=os.getenv(
                "MEDICAL_KB_GENERAL_COLLECTION", "medical_knowledge_v1"
            ).strip(),
            special_collection=os.getenv(
                "MEDICAL_KB_SPECIAL_COLLECTION", "medical_knowledge"
            ).strip(),
            embedding_model=os.getenv(
                "MEDICAL_KB_EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5"
            ).strip(),
            expected_dimension=int(os.getenv("MEDICAL_KB_EXPECTED_DIMENSION", "512")),
            expected_metric=os.getenv("MEDICAL_KB_EXPECTED_METRIC", "COSINE")
            .strip()
            .upper(),
        )

    def validate(self) -> None:
        if not self.enabled:
            return
        if not self.path:
            raise ValueError("MEDICAL_KB_PATH is required when MEDICAL_KB_ENABLED=true")
        if not Path(self.path).exists():
            raise ValueError(f"MEDICAL_KB_PATH does not exist: {self.path}")
        if not self.general_collection:
            raise ValueError("MEDICAL_KB_GENERAL_COLLECTION must not be empty")
        if not self.special_collection:
            raise ValueError("MEDICAL_KB_SPECIAL_COLLECTION must not be empty")
        if not self.embedding_model:
            raise ValueError("MEDICAL_KB_EMBEDDING_MODEL must not be empty")
        if self.expected_dimension <= 0:
            raise ValueError("MEDICAL_KB_EXPECTED_DIMENSION must be positive")
        if self.expected_metric != "COSINE":
            raise ValueError("MEDICAL_KB_EXPECTED_METRIC must be COSINE")

    def collection_mapping(
        self, *, logical_general: str, logical_special: str
    ) -> dict[str, str]:
        return {
            logical_general: self.general_collection,
            logical_special: self.special_collection,
            self.general_collection: self.general_collection,
            self.special_collection: self.special_collection,
        }
