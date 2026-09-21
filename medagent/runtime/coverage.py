from __future__ import annotations

from dataclasses import asdict, dataclass

from medagent.agents.base import WorkerResult
from medagent.context.contract import AnswerContract
from medagent.context.request_spec import RequestSpec
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


@dataclass(frozen=True, slots=True)
class RequestCoverage:
    required_request_items: list[str]
    covered_request_items: list[str]
    missing_request_items: list[str]
    request_item_answers: dict[str, str]

    @property
    def complete(self) -> bool:
        return not self.missing_request_items

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["user_request_complete"] = self.complete
        return value


def evaluate_request_coverage(
    request_spec: RequestSpec, workers: list[WorkerResult]
) -> RequestCoverage:
    """Count only schema-valid, non-empty answers actually returned by workers."""

    answers: dict[str, str] = {}
    valid_ids = {item.id for item in request_spec.items}
    for worker in workers:
        if not worker.success:
            continue
        for item in worker.request_item_answers:
            if item.request_item_id in valid_ids and item.answer.strip():
                answers.setdefault(item.request_item_id, item.answer.strip())
    required = request_spec.required_item_ids
    covered = [item.id for item in request_spec.items if item.id in answers]
    missing = [item for item in required if item not in answers]
    return RequestCoverage(required, covered, missing, answers)


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
