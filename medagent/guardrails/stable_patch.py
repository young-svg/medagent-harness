from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class StablePatchResult:
    answer: str
    applied: bool
    reason: str


def apply_stable_patch(answer: str, replacements: dict[str, str]) -> StablePatchResult:
    """Replace only exact stable IDs; ambiguity preserves the original."""
    updated = answer
    for stable_id, replacement in replacements.items():
        marker = f"[[{stable_id}]]"
        if updated.count(marker) != 1:
            return StablePatchResult(answer, False, f"unstable_or_missing_unit:{stable_id}")
        updated = updated.replace(marker, replacement)
    return StablePatchResult(updated, bool(replacements), "applied" if replacements else "no_edits")
