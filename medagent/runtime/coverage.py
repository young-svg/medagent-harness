from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from medagent.agents.base import WorkerResult
from medagent.context.contract import AnswerContract
from medagent.context.request_spec import RequestSpec
from medagent.planning.complexity import EVIDENCE_TOOLS, evidence_requirements
from medagent.planning.models import Subtask
from medagent.retrieval.evidence import EvidenceBundle
from medagent.runtime.final_answers import has_answer_content


def cited_evidence_ids(text: str) -> set[str]:
    return set(re.findall(r"\[EVIDENCE:([^\]\n]+)\]", text))


def evaluate_evidence_coverage(
    spec: RequestSpec, answers: dict[str, str], bundle: EvidenceBundle,
    allowed: dict[str, set[str]],
    *, subtask_id: str | None = None,
) -> dict[str, object]:
    """Deterministic provenance/citation gate, not entailment or medical verification."""
    items = []
    for item in spec.items:
        declaration = bundle.requirement_for(item.id)
        kinds = (evidence_requirements(item.text) if bundle.evidence_requirements is None
                 else {declaration.source_category} if declaration else set())
        required = declaration.required if declaration else item.required and bool(kinds)
        citations = cited_evidence_ids(answers.get(item.id, ""))
        receipts = [r for r in bundle.receipts(item.id, subtask_id)
                    if r["evidence_id"] in allowed.get(item.id, set())
                    and (subtask_id is not None or r["evidence_id"] in
                         bundle.accepted_by_task.get((r["subtask_id"], item.id), set()))]
        available = {r["evidence_id"] for r in receipts}
        records = [r for r in bundle.retrievals if item.id in r["request_item_ids"]]
        if citations - available:
            status = "invalid_citation"
        elif declaration and citations and any(not any(
            r["evidence_id"] == identifier and declaration.accepts_delivery(r) for r in receipts
        ) for identifier in citations):
            status = "missing_evidence_kind"
        elif not required:
            status = "not_required"
        elif available:
            status = "satisfied" if citations else "uncited_evidence"
            if declaration is None and citations and any(not any(
                r["tool"] in EVIDENCE_TOOLS[kind] and r["evidence_id"] in citations
                and (kind == "external" or r.get("source_kind") == kind)
                for r in receipts
            ) for kind in kinds):
                status = "missing_evidence_kind"
        elif not records:
            status = "not_retrieved"
        else:
            # Prefer an informative retrieval failure over a secondary missing citation.
            statuses = {r["status"] for r in records}
            status = next((s for s in ("disabled", "retrieval_error", "low_quality",
                                      "no_results") if s in statuses), "undelivered_evidence")
        used = [r for r in receipts if r["evidence_id"] in citations]
        items.append({"request_item_id": item.id, "required": required, "status": status,
                      "source_event_ids": list(dict.fromkeys(r["source_event_id"] for r in used)),
                      "handoff_event_ids": list(dict.fromkeys(
                          r["handoff_event_id"] for r in used if r.get("handoff_event_id"))),
                      "origin_request_item_ids": list(dict.fromkeys(
                          r["origin_request_item_id"] for r in used)),
                      "evidence_links": [{k: r[k] for k in (
                          "request_item_id", "origin_request_item_id",
                          "source_event_id", "handoff_event_id",
                      ) if k in r} for r in used]})
    return {"complete": all(v["status"] in {"satisfied", "not_required"} for v in items),
            "items": items}


def required_deliverable_ids(contract: AnswerContract) -> list[str]:
    """Resolve required contract text to the existing deliverable ID taxonomy."""

    return contract.required_deliverable_ids


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


def evaluate_final_request_coverage(
    request_spec: RequestSpec, answers: dict[str, str]
) -> RequestCoverage:
    ordered = {
        item.id: answers[item.id]
        for item in request_spec.items
        if item.id in answers and has_answer_content(answers[item.id])
    }
    required = request_spec.required_item_ids
    return RequestCoverage(
        required, list(ordered), [item for item in required if item not in ordered], ordered
    )


def evaluate_final_contract_coverage(
    contract: AnswerContract,
    request_spec: RequestSpec,
    subtasks: list[Subtask],
    final: RequestCoverage,
    worker_coverage: ContractCoverage,
) -> ContractCoverage:
    required = required_deliverable_ids(contract)
    covered: list[str] = []
    for deliverable in contract.requested_deliverables:
        ids = {
            item.id
            for item in request_spec.items
            if item.semantic_type == deliverable and (item.required or deliverable not in required)
        }
        if not ids:
            ids = {
                request_id
                for subtask in subtasks
                if deliverable in subtask.deliverable_ids
                for request_id in subtask.request_item_ids
                if request_id in request_spec.required_item_ids
            }
        if ids and ids.issubset(final.covered_request_items):
            covered.append(deliverable)
    return ContractCoverage(
        required,
        covered,
        [item for item in required if item not in covered],
        worker_coverage.successful_workers,
        worker_coverage.failed_workers,
    )
