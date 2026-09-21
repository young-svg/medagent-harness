from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import StrEnum


class EditAction(StrEnum):
    DELETE = "DELETE"
    REPLACE = "REPLACE"
    DOWNGRADE = "DOWNGRADE"


@dataclass(slots=True)
class StableUnit:
    id: str
    kind: str
    text: str
    group: str
    mixed: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class StableDraft:
    units: list[StableUnit] = field(default_factory=list)

    @classmethod
    def parse(cls, answer: str) -> StableDraft:
        units = []
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", answer) if part.strip()]
        for index, text in enumerate(paragraphs, 1):
            lower = text.casefold()
            question = "?" in text or "？" in text
            recommendation = any(
                word in lower
                for word in ("should", "recommend", "consider", "建议", "应当", "需要")
            )
            historical = any(
                word in lower
                for word in ("history of", "previously", "past diagnosis", "既往", "曾诊断")
            )
            conditional = any(
                word in lower for word in (" if ", "when ", "may", "could", "若", "如果", "可能")
            )
            kinds = [
                name
                for name, present in (
                    ("question", question),
                    ("recommendation", recommendation),
                    ("history", historical),
                    ("conditional", conditional),
                )
                if present
            ]
            units.append(
                StableUnit(
                    f"U{index}",
                    kinds[0] if len(kinds) == 1 else "claim",
                    text,
                    f"P{index}",
                    mixed=len(kinds) > 1,
                )
            )
        return cls(units)

    def render(self) -> str:
        return "\n\n".join(unit.text for unit in self.units if unit.text.strip())


@dataclass(slots=True)
class StableEdit:
    unit_id: str
    action: EditAction
    replacement: str = ""
    reason: str = ""


@dataclass(slots=True)
class StablePatchResult:
    answer: str
    applied: bool
    reason: str


def apply_stable_edits(draft: StableDraft, edits: list[StableEdit]) -> StablePatchResult:
    units = {unit.id: unit for unit in draft.units}
    for edit in edits:
        unit = units.get(edit.unit_id)
        if unit is None or unit.mixed:
            return StablePatchResult(draft.render(), False, f"unsafe_unit:{edit.unit_id}")
    for edit in edits:
        unit = units[edit.unit_id]
        if edit.action == EditAction.DELETE:
            unit.text = ""
        elif edit.action == EditAction.REPLACE:
            unit.text = edit.replacement
        elif edit.action == EditAction.DOWNGRADE:
            unit.text = edit.replacement or f"This is a hypothesis: {unit.text}"
    return StablePatchResult(draft.render(), bool(edits), "applied" if edits else "no_edits")


def apply_stable_patch(answer: str, replacements: dict[str, str]) -> StablePatchResult:
    draft = StableDraft.parse(answer)
    edits = [
        StableEdit(unit_id, EditAction.REPLACE, text) for unit_id, text in replacements.items()
    ]
    return apply_stable_edits(draft, edits)
