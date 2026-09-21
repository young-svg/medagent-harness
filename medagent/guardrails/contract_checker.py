from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from medagent.context.contract import AnswerContract
from medagent.context.evidence_ledger import EvidenceLedger
from medagent.guardrails.stable_patch import EditAction, StableDraft, StableEdit


@dataclass(slots=True)
class CheckResult:
    passed: bool
    issues: list[str] = field(default_factory=list)
    edits: list[StableEdit] = field(default_factory=list)

    @property
    def missing(self) -> list[str]:
        return [
            item.removeprefix("missing_deliverable:")
            for item in self.issues
            if item.startswith("missing_deliverable:")
        ]

    @property
    def edits_allowed(self) -> bool:
        return bool(self.edits)

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "issues": self.issues,
            "edits": [asdict(edit) for edit in self.edits],
        }


def _normalize_target(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", value.casefold()).strip()


class Guardrail:
    """Conservative public checker: detect broadly and edit only whole safe units."""

    _NEGATED = re.compile(
        r"(?:no|not|without|negative for|排除|无|未见)\s+([\w\u4e00-\u9fff -]{2,40})", re.I
    )

    def check(self, answer: str, contract: AnswerContract, ledger: EvidenceLedger) -> CheckResult:
        draft = StableDraft.parse(answer)
        issues: list[str] = []
        edits: list[StableEdit] = []
        lower_answer = answer.casefold()
        for deliverable in contract.requested_deliverables:
            words = deliverable.replace("_", " ").casefold()
            if words not in lower_answer and len(contract.requested_deliverables) > 1:
                issues.append(f"missing_deliverable:{deliverable}")
        source = " ".join(item.source_text for item in ledger.findings)
        negated_targets = {
            _normalize_target(match.group(1)) for match in self._NEGATED.finditer(source)
        }
        for unit in draft.units:
            if unit.kind in {"question", "recommendation", "history", "conditional"} or unit.mixed:
                continue
            normalized = _normalize_target(unit.text)
            for target in negated_targets:
                if target and target in normalized and not self._NEGATED.search(unit.text):
                    issues.append(f"contradiction:{unit.id}:{target}")
                    edits.append(
                        StableEdit(
                            unit.id,
                            EditAction.DOWNGRADE,
                            f"Unconfirmed hypothesis requiring verification: {unit.text}",
                            "same-target contradiction with an explicit patient fact",
                        )
                    )
                    break
        return CheckResult(not issues, issues, edits)


class ContractChecker(Guardrail):
    def check(
        self, answer: str, contract: AnswerContract, ledger: EvidenceLedger | None = None
    ) -> CheckResult:
        return super().check(answer, contract, ledger or EvidenceLedger())
