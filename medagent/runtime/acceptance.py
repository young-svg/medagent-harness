"""Shared internal completion gates; no architecture-specific acceptance shortcuts."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from medagent.agents.base import WorkerResult
from medagent.context.contract import AnswerContract
from medagent.context.evidence_ledger import EvidenceLedger
from medagent.context.request_spec import RequestSpec
from medagent.guardrails.contract_checker import CheckResult, Guardrail
from medagent.guardrails.sanitizer import sanitize_answer
from medagent.guardrails.stable_patch import StableDraft, apply_stable_edits
from medagent.observability.tracer import TraceRecorder
from medagent.planning.complexity import ResponseProfile, TaskComplexityProfile
from medagent.planning.models import Plan
from medagent.retrieval.evidence import EvidenceBundle
from medagent.runtime.config import RuntimeConfig
from medagent.runtime.coverage import (
    ContractCoverage,
    RequestCoverage,
    evaluate_contract_coverage,
    evaluate_evidence_coverage,
    evaluate_final_contract_coverage,
    evaluate_final_request_coverage,
    evaluate_request_coverage,
)
from medagent.runtime.final_answers import has_answer_content, parse_synthesis_answers
from medagent.runtime.semantic_validation import SemanticValidator


@dataclass(slots=True)
class AcceptanceResult:
    coverage: ContractCoverage
    request_coverage: RequestCoverage
    final_coverage: RequestCoverage
    final_contract: ContractCoverage
    evidence_validation: dict[str, Any]
    semantic: dict[str, Any]
    final_answer: str
    status: str
    final_check: CheckResult | None = None
    failure_reason: str | None = None

    @property
    def preflight_failed(self) -> bool:
        return self.failure_reason is not None


class FinalAcceptance:
    def __init__(self, config: RuntimeConfig, *, finalizer: Callable | None = None) -> None:
        self.config = config
        self.finalize = finalizer or finalize_answers

    async def evaluate(
        self, *, question: str, description: str, request_spec: RequestSpec,
        contract: AnswerContract, restrictions: list[str], plan: Plan,
        workers: list[WorkerResult], ledger: EvidenceLedger, evidence_bundle: EvidenceBundle,
        trace: TraceRecorder, validator: SemanticValidator, semantic: dict[str, Any],
        complexity: TaskComplexityProfile, response_profile: ResponseProfile,
        synthesize: Callable[..., Awaitable[str]] | None = None,
    ) -> AcceptanceResult:
        successful = [item for item in workers if item.success and item.answer.strip()]
        coverage = evaluate_contract_coverage(contract, plan.subtasks, workers)
        trace.record("contract_coverage", coverage.to_dict(), stage="contract_completion")
        request_coverage = evaluate_request_coverage(request_spec, workers)
        trace.record(
            "request_coverage",
            request_coverage.to_dict(),
            stage="request_completion",
        )
        dependency_blocked = any(item.worker_status == "dependency_blocked" for item in workers)
        evidence_validation = evaluate_evidence_coverage(
            request_spec, request_coverage.request_item_answers, evidence_bundle,
            evidence_bundle.accepted_by_request,
        )
        if (not successful or not request_coverage.complete
                or not coverage.complete or dependency_blocked):
            for item_check in evidence_validation["items"]:
                if item_check["request_item_id"] not in request_coverage.covered_request_items:
                    for task_check in evidence_bundle.worker_checks.values():
                        failed = next((v for v in task_check["items"]
                                       if v["request_item_id"] == item_check["request_item_id"]
                                       and v["status"] not in {
                                           "satisfied", "not_required"}), None)
                        if failed:
                            item_check.update(failed)
                            evidence_validation["complete"] = False
                            break
            if self.config.semantic_validation_enabled:
                failed_request_ids = {
                    request_id
                    for subtask, worker in zip(plan.subtasks, workers, strict=True)
                    if not worker.success
                    for request_id in subtask.request_item_ids
                }
                semantic = {
                    "status": "not_run", "reason": "structural_or_execution_failure",
                    "complete": False, "calls": validator.calls,
                    "items": [{
                        "request_item_id": item.id,
                        "status": ("execution_error" if item.id in failed_request_ids
                                   else "unanswered"),
                    } for item in request_spec.items],
                }
            else:
                # An empty optional-only contract must not hide execution failure.
                semantic = {**semantic, "complete": False}
            trace.record("semantic_acceptance", semantic, stage="final_completion")
            trace.record("evidence_acceptance", evidence_validation, stage="final_completion")
            # No final body is emitted on this path. Keep Worker coverage as
            # diagnostic input, but report empty final coverage consistently.
            final_coverage = evaluate_final_request_coverage(request_spec, {})
            final_contract = evaluate_final_contract_coverage(
                contract, request_spec, plan.subtasks, final_coverage, coverage
            )
            trace.record(
                "final_request_coverage",
                {
                    **final_coverage.to_dict(),
                    "parse_status": "not_generated",
                    "fallback_attempted_request_items": [],
                    "recovered_request_items": [],
                },
                stage="final_completion",
            )
            trace.record(
                "final_contract_coverage", final_contract.to_dict(), stage="final_completion"
            )
            if dependency_blocked:
                failure_reason = "dependency_blocked"
            elif not request_coverage.complete:
                failure_reason = "missing_required_request_items"
            else:
                failure_reason = "missing_required_deliverables"
            trace.record(
                "error",
                {
                    "failure_stage": "request_completion",
                    "failure_reason": failure_reason,
                    "missing_required_request_items": (request_coverage.missing_request_items),
                    "missing_required_deliverables": (coverage.missing_required_deliverables),
                },
                stage="contract_completion",
            )
            return AcceptanceResult(
                coverage, request_coverage, final_coverage, final_contract,
                evidence_validation, semantic, "", "incomplete", failure_reason=failure_reason,
            )

        if len(successful) >= 2 and synthesize is not None:
            final_draft = await synthesize(
                question,
                request_spec,
                contract.to_dict(),
                successful,
                trace,
                complexity,
                response_profile,
            )
            answers, parse_status = parse_synthesis_answers(final_draft, request_spec)
        else:
            answers = dict(request_coverage.request_item_answers)
            parse_status = "single_worker" if len(successful) == 1 else "worker_answers"
        final_answers, check, attempted_ids = self.finalize(
            answers,
            request_coverage.request_item_answers,
            request_spec,
            contract,
            ledger,
            trace,
        )
        final_coverage = evaluate_final_request_coverage(request_spec, final_answers)
        recovered_ids = [
            item for item in final_coverage.covered_request_items if item in attempted_ids
        ]
        final_contract = evaluate_final_contract_coverage(
            contract, request_spec, plan.subtasks, final_coverage, coverage
        )
        final_answer = "\n\n".join(final_coverage.request_item_answers.values())
        evidence_validation = evaluate_evidence_coverage(
            request_spec, final_coverage.request_item_answers, evidence_bundle,
            evidence_bundle.accepted_by_request,
        )
        trace.record("evidence_acceptance", evidence_validation, stage="final_completion")
        status = (
            "completed" if final_coverage.complete and final_contract.complete
            and evidence_validation["complete"] else "incomplete"
        )
        # Safety hard blocks are independent of the optional semantic model gate.
        final_check_input = trace.record(
            "checker_input", {"draft": final_answer, "phase": "final"},
            stage="guardrail", agent="guardrail",
        )
        final_check = Guardrail().check(final_answer, contract, ledger)
        final_check.blocking_issues = list(dict.fromkeys(
            check.blocking_issues + final_check.blocking_issues
        ))
        if final_check.blocking_issues:
            final_check.passed = False
        trace.record(
            "checker_result", final_check.to_dict(), stage="guardrail", agent="guardrail",
            parent_event_id=final_check_input,
        )
        if final_check.blocking_issues:
            semantic = {
                **semantic, "status": "blocked", "reason": "guardrail_hard_block",
                "complete": False, "calls": validator.calls,
                "blocking_issues": final_check.blocking_issues,
            }
        elif not evidence_validation["complete"]:
            semantic = {**semantic, "complete": False, "status": "blocked",
                        "reason": "evidence_requirement_unsatisfied"}
        elif self.config.semantic_validation_enabled:
            semantic = await validator.accept(
                question=question, description=description, spec=request_spec,
                contract=contract, restrictions=restrictions, answers=final_answers,
                guardrail=final_check,
            )
        if semantic["complete"] is False:
            status = "incomplete"
        trace.record("semantic_acceptance", semantic, stage="final_completion")
        trace.record(
            "final_request_coverage",
            {
                **final_coverage.to_dict(),
                "parse_status": parse_status,
                "fallback_attempted_request_items": attempted_ids,
                "recovered_request_items": recovered_ids,
            },
            stage="final_completion",
        )
        trace.record(
            "final_contract_coverage", final_contract.to_dict(), stage="final_completion"
        )
        if status == "incomplete":
            trace.record(
                "error",
                {
                    "failure_stage": "final_completion",
                    "failure_reason": "missing_safe_final_answers",
                    "missing_required_request_items": final_coverage.missing_request_items,
                },
                stage="final_completion",
            )

        return AcceptanceResult(
            coverage, request_coverage, final_coverage, final_contract,
            evidence_validation, semantic, final_answer, status, final_check=final_check,
        )


def finalize_answers(
    answers: dict[str, str],
    worker_answers: dict[str, str],
    request_spec: RequestSpec,
    contract: AnswerContract,
    ledger: EvidenceLedger,
    trace: TraceRecorder,
    *,
    sanitizer: Callable[[str], str] = sanitize_answer,
) -> tuple[dict[str, str], CheckResult, list[str]]:
    sanitize_answer = sanitizer
    attempted: list[str] = []
    blocking: list[str] = []
    # One initial pass and at most one deterministic repair after safety edits.
    # Every fallback goes through the same sanitizer and medical Guardrail.
    for attempt in range(2):
        answers = {
            item.id: sanitize_answer(answers[item.id])
            for item in request_spec.items
            if item.id in answers
        }
        answers = {key: value for key, value in answers.items() if has_answer_content(value)}
        for request_id in request_spec.required_item_ids:
            if request_id not in answers and request_id in worker_answers:
                fallback = sanitize_answer(worker_answers[request_id])
                if has_answer_content(fallback):
                    answers[request_id] = fallback
                    if request_id not in attempted:
                        attempted.append(request_id)
        ordered = [
            (item.id, answers[item.id]) for item in request_spec.items if item.id in answers
        ]
        draft_text = "\n\n".join(value for _, value in ordered)
        draft = StableDraft.parse(draft_text)
        owners = [
            request_id for request_id, value in ordered for _ in StableDraft.parse(value).units
        ]
        checker_input = trace.record(
            "checker_input",
            {"draft": draft_text, "attempt": attempt},
            stage="guardrail",
            agent="guardrail",
        )
        check = Guardrail().check(draft_text, contract, ledger)
        patch = apply_stable_edits(draft, check.edits)
        if check.edits and not patch.applied:
            check.blocking_issues.append("safety_patch_rejected")
        # Current protocol has no explicit hard-block resolution operation.
        # A later check or restored Worker draft cannot implicitly clear a block.
        blocking = list(dict.fromkeys(blocking + check.blocking_issues))
        check.blocking_issues = list(blocking)
        if blocking:
            check.passed = False
        trace.record(
            "checker_result",
            check.to_dict(),
            stage="guardrail",
            agent="guardrail",
            parent_event_id=checker_input,
        )
        trace.record(
            "patch_applied",
            {"applied": patch.applied, "reason": patch.reason},
            stage="guardrail",
            agent="guardrail",
        )
        # Ownership is retained through stable-unit edits. If an implementation
        # changes untracked text, fail closed rather than claiming old coverage.
        final: dict[str, str] = {}
        if len(owners) == len(draft.units) and patch.answer == draft.render():
            for owner, unit in zip(owners, draft.units, strict=True):
                text = sanitize_answer(unit.text)
                if text:
                    final[owner] = "\n\n".join(filter(None, [final.get(owner), text]))
            final = {key: value for key, value in final.items() if has_answer_content(value)}
            rendered = "\n\n".join(final.values())
            if sanitize_answer(rendered) != rendered:
                final = {}  # Cross-section sanitization invalidates ownership.
        if evaluate_final_request_coverage(request_spec, final).complete:
            return final, check, attempted
        answers = final
    return answers, check, attempted
