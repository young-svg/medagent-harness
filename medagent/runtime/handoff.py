from __future__ import annotations

from dataclasses import asdict, dataclass

from medagent.agents.base import RequestItemAnswer, WorkerResult
from medagent.context.request_spec import RequestSpec
from medagent.guardrails.sanitizer import sanitize_answer
from medagent.guardrails.untrusted_context import contains_instruction_spoof
from medagent.planning.complexity import (
    EVIDENCE_TOOLS,
    evidence_requirements,
    permits_handoff_reuse,
)
from medagent.planning.models import Subtask
from medagent.retrieval.evidence import EvidenceBundle
from medagent.runtime.coverage import cited_evidence_ids
from medagent.runtime.final_answers import has_answer_content


@dataclass(frozen=True, slots=True)
class WorkerHandoff:
    source_subtask_id: str
    worker: str
    request_item_answers: tuple[RequestItemAnswer, ...]
    status: str
    source_event_id: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def evidence_transfers(
    handoff: WorkerHandoff, target: Subtask, spec: RequestSpec, bundle: EvidenceBundle,
) -> list[dict[str, object]]:
    """Authorize only source-backed explicit reuse; never re-label a fresh lookup."""
    transfers = []
    for item in spec.items:
        if item.id not in target.request_item_ids:
            continue
        declaration = bundle.requirement_for(item.id)
        kinds = (evidence_requirements(item.text) if bundle.evidence_requirements is None
                 else {declaration.source_category} if declaration else set())
        for answer in handoff.request_item_answers:
            if bundle.evidence_requirements is not None:
                if (declaration is None or declaration.execution != "reuse_only"
                        or answer.request_item_id not in declaration.upstream_request_item_ids):
                    continue
            elif answer.request_item_id != item.id and not permits_handoff_reuse(item.text):
                continue
            accepted = bundle.accepted_by_task.get(
                (handoff.source_subtask_id, answer.request_item_id), set(),
            ) & cited_evidence_ids(answer.answer)
            for receipt in bundle.receipts(answer.request_item_id, handoff.source_subtask_id):
                if receipt["evidence_id"] not in accepted:
                    continue
                if declaration is not None and not declaration.accepts_source(receipt):
                    continue
                if declaration is None and kinds and not any(receipt["tool"] in EVIDENCE_TOOLS[k]
                                     and (k == "external" or receipt.get("source_kind") == k)
                                     for k in kinds):
                    continue
                transfer = {**receipt, "subtask_id": target.subtask_id,
                            "request_item_id": item.id}
                if declaration is not None:
                    transfer["upstream_request_item_id"] = answer.request_item_id
                transfers.append(transfer)
    return transfers


def build_handoff(
    source: Subtask, result: WorkerResult, request_spec: RequestSpec
) -> WorkerHandoff | None:
    """Transfer only validated, sanitized public answers, never Worker internals."""
    if (
        not result.success
        or not result.trace_event_id
        or result.subtask_id != source.subtask_id
        or result.worker != source.assigned_agent
    ):
        return None
    allowed = set(source.request_item_ids) & {item.id for item in request_spec.items}
    required = {item.id for item in request_spec.items if item.required and item.id in allowed}
    answers: list[RequestItemAnswer] = []
    seen: set[str] = set()
    for item in result.request_item_answers:
        if (
            not isinstance(item, RequestItemAnswer)
            or not isinstance(item.request_item_id, str)
            or not isinstance(item.answer, str)
            or item.request_item_id not in allowed
            or item.request_item_id in seen
        ):
            return None
        seen.add(item.request_item_id)
        clean = sanitize_answer(item.answer)
        if contains_instruction_spoof(clean):
            return None
        if has_answer_content(clean):
            answers.append(RequestItemAnswer(item.request_item_id, clean))
    if not answers or not required.issubset({item.request_item_id for item in answers}):
        return None
    return WorkerHandoff(
        source.subtask_id, result.worker, tuple(answers), "success", result.trace_event_id
    )
