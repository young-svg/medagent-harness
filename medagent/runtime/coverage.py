from __future__ import annotations

from dataclasses import asdict, dataclass

from medagent.agents.base import WorkerResult
from medagent.context.contract import AnswerContract
from medagent.planning.models import Subtask


def _coverage_key(value: str) -> str:
    return " ".join(value.replace("_", " ").casefold().split())


def required_deliverable_ids(contract: AnswerContract) -> list[str]:
    """Resolve required contract text to the existing deliverable ID taxonomy."""

    required_keys = {_coverage_key(item) for item in contract.must_cover}
    required_keys.update(
        _coverage_key(item.item)
        for item in contract.coverage_checklist
        if item.priority == "MUST"
    )
    return [
        deliverable
        for deliverable in contract.requested_deliverables
        if _coverage_key(deliverable) in required_keys
    ]


@dataclass(frozen=True, slots=True)
class ContractCoverage:
    required_deliverable_ids: list[str]
    covered_deliverable_ids: list[str]
    missing_required_deliverables: list[str]
    successful_workers: int
    failed_workers: int

    @property
    def complete(self) -> bool:
        return not self.missing_required_deliverables

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def evaluate_contract_coverage(
    contract: AnswerContract,
    subtasks: list[Subtask],
    workers: list[WorkerResult],
) -> ContractCoverage:
    """Compute coverage only from successful workers and explicit subtask mappings."""

    covered: set[str] = set()
    for subtask, worker in zip(subtasks, workers, strict=True):
        if worker.success and worker.answer.strip():
            covered.update(subtask.deliverable_ids)
    required = required_deliverable_ids(contract)
    covered_in_contract_order = [
        item for item in contract.requested_deliverables if item in covered
    ]
    missing = [item for item in required if item not in covered]
    successful_workers = sum(worker.success and bool(worker.answer.strip()) for worker in workers)
    return ContractCoverage(
        required,
        covered_in_contract_order,
        missing,
        successful_workers,
        len(workers) - successful_workers,
    )
