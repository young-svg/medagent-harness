from __future__ import annotations

from dataclasses import asdict, dataclass

from medagent.agents.base import RequestItemAnswer, WorkerResult
from medagent.context.request_spec import RequestSpec
from medagent.guardrails.sanitizer import sanitize_answer
from medagent.planning.models import Subtask
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
        if has_answer_content(clean):
            answers.append(RequestItemAnswer(item.request_item_id, clean))
    if not answers or not required.issubset({item.request_item_id for item in answers}):
        return None
    return WorkerHandoff(
        source.subtask_id, result.worker, tuple(answers), "success", result.trace_event_id
    )
