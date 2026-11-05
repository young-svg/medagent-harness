"""Allowlisted metadata projection. This is data minimization, not medical de-identification."""
from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from medagent.context.contract import DELIVERABLES
from medagent.observability.schema import EVENT_TYPES
from medagent.tools.schemas import TOOL_SCHEMAS

_NUMBERS = set("""latency_ms max_tokens temperature attempt_index planner_max_tokens
length_recovery_count infrastructure_retry_count provider_attempt_count protocol_recovery_count
protocol_recovery_success_count tool_calls tool_call_count content_length successful_workers
failed_workers event_count llm_calls tokens calls messages_added status_code top_k
prompt_tokens completion_tokens total_tokens reasoning_tokens cached_tokens
worker_infrastructure_retry_count workers_recovered_after_infra_retry
workers_failed_after_infra_retry
retained_history_messages available_history_messages count_trimmed_history_messages
budget_trimmed_history_messages estimated_history_tokens history_token_budget
quarantined_history_messages injected_history_messages count""".split())
_NUMBERS.update({"quarantined_count", "blocking_issue_count", "context_risk_count"})
_NUMBERS.update({"model_round_count", "max_model_rounds", "max_subtasks",
                 "max_concurrent_workers", "max_tool_calls", "worker_timeout_seconds",
                 "tool_error_count", "attempt_count", "retry_index", "max_retries"})
_NUMBERS.update({"task_index", "delay_seconds"})
_NUMBERS.add("admitted_count")
_BOOLS = set("""complete user_request_complete contract_complete structural_user_request_complete
structural_contract_complete worker_user_request_complete passed success applied
memory_enabled""".split())
_BOOLS.add("required")
_STATUSES = set("""running completed incomplete failed disabled accepted rejected uncertain
execution_error blocked not_run success provider_error dependency_blocked not_started
completed_first_attempt completed_after_length_recovery completed_after_protocol_recovery
length_exhausted satisfied unanswered off_topic insufficient_information metadata raw
structural_only stop length tool_calls fallback valid invalid""".split())
_STATUSES.add("generation_error")
_STATUSES.update({"worker_timeout", "protocol_error", "budget_exhausted", "tool_error"})
_STATUSES.update({"evidence_error", "not_required", "not_retrieved", "retrieval_error",
                  "low_quality", "no_results", "uncited_evidence", "invalid_citation",
                  "undelivered_evidence"})
_STATUSES.add("missing_evidence_kind")
_REASONS = {"instruction_spoof_in_worker_answer", "guardrail_hard_block", "dependency_failed",
            "dependency_blocked", "invalid_or_empty_worker_response", "provider_error",
            "semantic_budget_exhausted", "semantic_input_budget_exceeded",
            "invalid_source_inventory"}
_REASONS.update({"model_round_budget_exhausted", "tool_call_budget_exhausted",
                 "subtask_budget_exhausted", "worker_timeout", "protocol_error",
                 "execution_error", "generation_length_exhausted"})
_REASONS.update({"invalid_tool_call_id", "duplicate_tool_call_id"})
_REASONS.add("evidence_requirement_unsatisfied")
_REASONS.update({"model_parameters_drift", "runtime_configuration_drift",
                 "memory_configuration_drift", "tool_permissions_drift"})
_REASONS.add("retrieval_conditions_drift")
_ID_LISTS = set("""required_request_items covered_request_items missing_request_items
missing_required_request_items required_deliverable_ids covered_deliverable_ids
missing_required_deliverables answered_request_item_ids""".split())
# Public role specifications only; this is not a general skill/path allowlist.
_ROLE_SPEC_NAMES = {
    "diagnostic_agent": "diagnosis",
    "consultation_agent": "consultation",
    "research_agent": "research",
    "synthesizer": "synthesis",
}


def _role_spec_manifest(value: Any) -> dict[str, dict[str, str]]:
    if not isinstance(value, dict):
        return {}
    result = {}
    for agent, name in _ROLE_SPEC_NAMES.items():
        entry = value.get(agent)
        if not isinstance(entry, dict):
            continue
        digest = entry.get("skill_sha256")
        if (entry.get("skill_name") == name and isinstance(digest, str)
                and re.fullmatch(r"[0-9a-f]{64}", digest)):
            # Copy only these scalar fields, never instructions, paths or nested data.
            result[agent] = {"skill_name": name, "skill_sha256": digest}
    return result


def _event_uuid(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


def metadata_payload(value: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in value.items():
        if key in {"plan_sha256", "request_sha256", "execution_conditions_sha256"}:
            if isinstance(item, str) and re.fullmatch(r"[0-9a-f]{64}", item):
                result[key] = item
        elif key == "plan_source" and item in ("planner_policy", "frozen_replay"):
            result[key] = item
        elif key == "request_source" and item == "frozen":
            result[key] = item
        elif key == "planner_call_count" and type(item) is int and item >= 0:
            result[key] = item
        elif key == "skills":
            manifest = _role_spec_manifest(item)
            if manifest:
                result[key] = manifest
        elif key in _NUMBERS and (item is None or type(item) in {int, float}):
            result[key] = item
        elif key in _BOOLS and (item is None or type(item) is bool):
            result[key] = item
        elif (key in {"status", "worker_status", "generation_status", "finish_reason",
                      "assurance", "trace_content_mode"}
              and isinstance(item, str) and item in _STATUSES):
            result[key] = item
        elif key in {"type", "error_type", "error"} and isinstance(item, str):
            if re.fullmatch(r"[A-Za-z_]{1,64}(?:Error|Exception)", item):
                result[key] = item
        elif key == "name" and isinstance(item, str) and item in TOOL_SCHEMAS:
            result[key] = item
        elif key in {"reason", "failure_reason"} and isinstance(item, str) and item in _REASONS:
            result[key] = item
        elif key == "blocking_issues" and isinstance(item, list):
            result["blocking_issue_count"] = len(item)
        elif key in {"retry_type", "recovery_type"} and item in (
            None, "infrastructure_retry", "length_completion_recovery",
            "structured_output_completion_recovery", "worker_protocol_recovery",
        ):
            result[key] = item
        elif key == "outcome" and item in ("success", "error"):
            result[key] = item
        elif key == "execution_failures" and isinstance(item, list):
            result[key] = [metadata_payload(v) for v in item if isinstance(v, dict)]
        elif (key == "failure_stage" and isinstance(item, str)
              and item in {"planning", "worker", "synthesis", "evaluation"}):
            result[key] = item
        elif (key in {"usage", "error", "http", "semantic_validation",
                      "evidence_validation", "result"}
              and isinstance(item, dict)):
            result[key] = metadata_payload(item)
        elif key in {"items", "evidence_links"} and isinstance(item, list):
            result[key] = [metadata_payload(v) for v in item if isinstance(v, dict)]
        elif (key in {"request_item_id", "origin_request_item_id"} and isinstance(item, str)
              and re.fullmatch(r"RQ\d{1,3}", item)):
            result[key] = item
        elif key in {"request_item_ids", "origin_request_item_ids"} and isinstance(item, list):
            result[key] = [v for v in item if isinstance(v, str) and re.fullmatch(r"RQ\d{1,3}", v)]
        elif key in _ID_LISTS and isinstance(item, list):
            result[key] = [v for v in item if isinstance(v, str) and (
                v in DELIVERABLES or re.fullmatch(r"RQ\d{1,3}", v)
            )]
        elif key == "events" and isinstance(item, list):
            result[key] = [v for v in item if isinstance(v, str) and v in EVENT_TYPES]
        elif (key in {"run_id", "wait_event_id", "source_event_id",
                      "handoff_event_id", "retrieval_id"} and _event_uuid(item)):
            result[key] = item
        elif key in {"source_event_ids", "handoff_event_ids"} and isinstance(item, list):
            result[key] = [v for v in item if _event_uuid(v)]
    if "result" in value and isinstance(value["result"], dict):
        result["success"] = "error" not in value["result"]
    return result
