from __future__ import annotations

from dataclasses import dataclass, field

from medagent.context.contract import AnswerContract


@dataclass(slots=True)
class CheckResult:
    passed: bool
    missing: list[str] = field(default_factory=list)
    edits_allowed: bool = False

    def to_dict(self) -> dict[str, object]:
        return {"passed": self.passed, "missing": self.missing, "edits_allowed": self.edits_allowed}


class ContractChecker:
    """Detection is separate from edit permission."""

    def check(self, answer: str, contract: AnswerContract) -> CheckResult:
        if not answer.strip():
            return CheckResult(False, list(contract.must_cover), False)
        missing = [item for item in contract.must_cover if item.casefold() not in answer.casefold()]
        # The stable checker may report semantic gaps, but an automatic edit is
        # only safe for exact, stable structural units.
        return CheckResult(not missing, missing, False)
