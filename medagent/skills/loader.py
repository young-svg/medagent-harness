from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ProceduralSkill:
    name: str
    instructions: str
    path: Path


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
