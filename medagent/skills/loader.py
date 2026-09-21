from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ProceduralSkill:
    name: str
    instructions: str
    path: Path

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.instructions.encode("utf-8")).hexdigest()


def load_skill(path: str | Path) -> ProceduralSkill:
    """Explicitly load one public role specification; directories are not injected."""
    target = Path(path)
    if (
        target.suffix != ".md"
        or target.name not in {"diagnosis.md", "consultation.md", "research.md", "synthesis.md"}
        or not target.is_file()
    ):
        raise ValueError("a concrete public role specification is required")
    return ProceduralSkill(target.stem, target.read_text(encoding="utf-8"), target)


def load_public_skills() -> dict[str, ProceduralSkill]:
    specs = Path(__file__).resolve().parent / "specs"
    return {
        "diagnostic_agent": load_skill(specs / "diagnosis.md"),
        "consultation_agent": load_skill(specs / "consultation.md"),
        "research_agent": load_skill(specs / "research.md"),
        "synthesizer": load_skill(specs / "synthesis.md"),
    }
