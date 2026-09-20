from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ProceduralSkill:
    name: str
    instructions: str
    path: Path


def load_skill(path: str | Path) -> ProceduralSkill:
    """Explicitly load one procedural skill; directories are never auto-injected."""
    target = Path(path)
    if target.name != "SKILL.md" or not target.is_file():
        raise ValueError("a concrete SKILL.md file is required")
    return ProceduralSkill(target.parent.name, target.read_text(encoding="utf-8"), target)
