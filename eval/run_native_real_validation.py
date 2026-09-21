from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from medagent.observability.replay import read_trace
from medagent.observability.tracer import redact_trace_value
from medagent.runtime.config import RuntimeConfig
from medagent.runtime.native_engine import NativeMedAgentEngine

REQUESTED_MODEL = "deepseek-v4-flash"
EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"
GENERIC_COLLECTION = "medical_knowledge_v1"
SPECIAL_COLLECTION = "medical_knowledge"
TOP_K = 5
ADMISSION_THRESHOLD = 0.63
MAX_INFRASTRUCTURE_RETRIES = 2
TARGET_IDS = [f"MED-{number:03d}" for number in range(41, 61)]
VALID_WORKERS = {"diagnostic_agent", "consultation_agent", "research_agent"}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def append_log(path: Path, event: str, **payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = redact_trace_value({"timestamp": utc_now(), "event": event, **payload})
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def git_output(repo: Path, *arguments: str) -> str:
    return subprocess.check_output(
        ["git", *arguments], cwd=repo, text=True, encoding="utf-8"
    ).strip()


def production_source_sha(repo: Path) -> tuple[str, int]:
    relative_files = git_output(repo, "ls-files", "medagent", "api").splitlines()
    digest = hashlib.sha256()
    for relative in sorted(relative_files):
        digest.update(relative.replace("\\", "/").encode("utf-8"))
        digest.update(b"\0")
        digest.update((repo / relative).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest(), len(relative_files)


def private_dependency_hits(repo: Path) -> list[str]:
    pattern = re.compile(
        r"(^|\s)(from|import)\s+(swarm|legacy_v2)|medix-agent-swarm|"
        r"private runtime bridge|sys\.path",
        re.IGNORECASE,
    )
    hits = []
    for base_name in ("medagent", "api"):
        for path in (repo / base_name).rglob("*.py"):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if pattern.search(line):
                    hits.append(f"{path.relative_to(repo).as_posix()}:{number}")
    return hits


def load_allowed_cases(path: Path) -> list[dict[str, str]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        allowed = {
            "eval_id": str(raw["eval_id"]),
            "description": str(raw["description"]),
            "question": str(raw["question"]),
        }
        if allowed["eval_id"] in TARGET_IDS:
            rows.append(allowed)
    rows.sort(key=lambda item: TARGET_IDS.index(item["eval_id"]))
    if [row["eval_id"] for row in rows] != TARGET_IDS:
        raise RuntimeError("benchmark input does not contain exactly MED-041 through MED-060")
    return rows


def event_counts(events: list[dict[str, Any]]) -> dict[str, int]:
    return dict(Counter(str(event["event_type"]) for event in events))


def summarize_trace(
    trace_dir: Path, result: dict[str, Any] | None = None
) -> dict[str, Any]:
    events = read_trace(trace_dir)
    counts = event_counts(events)
    requests = [event for event in events if event["event_type"] == "llm_request"]
    responses = [event for event in events if event["event_type"] == "llm_response"]
    response_parents = {event.get("parent_event_id") for event in responses}
    unclosed = [
        event["event_id"] for event in requests if event["event_id"] not in response_parents
    ]
    route_event = next((event for event in events if event["event_type"] == "route_selected"), None)
    plan_event = next((event for event in events if event["event_type"] == "plan_created"), None)
    checker_event = next(
        (event for event in reversed(events) if event["event_type"] == "checker_result"), None
    )
    run_end = next((event for event in reversed(events) if event["event_type"] == "run_end"), None)
    retrieval_results = [
        event for event in events if event["event_type"] == "retrieval_result"
    ]
    raw_candidates = sum(
        len(event["payload"].get("raw_candidates") or []) for event in retrieval_results
    )
    admission = [
        item
        for event in retrieval_results
        for item in (event["payload"].get("admission") or [])
    ]
    admitted = sum(bool(item.get("admitted")) for item in admission)
    usage = [event["payload"].get("usage") or {} for event in responses]
    total_tokens = sum(int(item.get("total_tokens") or 0) for item in usage)
    plan = (plan_event or {}).get("payload") or {}
    subtasks = list(plan.get("subtasks") or [])
    dispatchable = bool(subtasks) and all(
        item.get("assigned_agent") in VALID_WORKERS for item in subtasks
    )
    required = {
        "run_start",
        "context_built",
        "memory_read",
        "contract_built",
        "evidence_ledger_built",
        "llm_request",
        "llm_response",
        "plan_created",
        "route_selected",
        "worker_draft",
        "checker_result",
        "final_answer",
        "run_end",
    }
    errors = [
        event["payload"].get("error")
        for event in responses
        if event["payload"].get("error")
    ]
    resolved_models = sorted(
        {
            str(event["payload"]["resolved_model"])
            for event in responses
            if event["payload"].get("resolved_model")
        }
    )
    requested_models = sorted(
        {
            str(event["payload"]["requested_model"])
            for event in requests
            if event["payload"].get("requested_model")
        }
    )
    evidence_cards = ((result or {}).get("presentation") or {}).get("evidence_cards") or []
    return {
        "run_id": events[0]["run_id"] if events else None,
        "trace_dir": trace_dir.as_posix(),
        "status": ((run_end or {}).get("payload") or {}).get("status", "incomplete"),
        "event_counts": counts,
        "route_mode": ((route_event or {}).get("payload") or {}).get("mode"),
        "route_agents": ((route_event or {}).get("payload") or {}).get("workers") or [],
        "planned_subtasks": subtasks,
        "planner_parse_failure": bool(plan.get("fallback_reason")),
        "dispatchable_plan": dispatchable,
        "llm_request_count": len(requests),
        "llm_response_count": len(responses),
        "llm_failed_response_count": len(errors),
        "llm_errors": errors,
        "unclosed_llm_request_ids": unclosed,
        "requested_models": requested_models,
        "resolved_models": resolved_models,
        "total_tokens": total_tokens,
        "tool_call_count": counts.get("tool_call", 0),
        "retrieval_query_count": counts.get("retrieval_query", 0),
        "retrieval_result_count": len(retrieval_results),
        "raw_candidate_count": raw_candidates,
        "admission_candidate_count": len(admission),
        "admitted_item_count": admitted,
        "evidence_card_count": len(evidence_cards),
        "evidence_cards_have_provenance": bool(evidence_cards)
        and all(
            bool(card.get("source"))
            and bool(card.get("title"))
            and bool(card.get("section"))
            and bool(card.get("text_preview"))
            for card in evidence_cards
        ),
        "checker_edit_count": len(((checker_event or {}).get("payload") or {}).get("edits") or []),
        "latency_ms": ((run_end or {}).get("payload") or {}).get("latency_ms"),
        "trace_complete": required.issubset(counts)
        and not unclosed
        and len(requests) == len(responses),
    }


def latest_new_trace(trace_root: Path, before: set[Path]) -> Path | None:
    candidates = [path for path in trace_root.iterdir() if path.is_dir() and path not in before]
    return max(candidates, key=lambda path: path.stat().st_mtime_ns) if candidates else None


def infrastructure_reason(error: BaseException, trace_dir: Path | None) -> str | None:
    parts = [type(error).__name__, str(error)]
    if trace_dir and (trace_dir / "trace.jsonl").is_file():
        for event in read_trace(trace_dir):
            if event["event_type"] == "llm_response" and event["payload"].get("error"):
                parts.append(json.dumps(event["payload"]["error"], ensure_ascii=False))
    text = " ".join(parts).casefold()
    if any(word in text for word in ("connecterror", "connectionerror", "connection refused")):
        return "connection"
    if any(word in text for word in ("timeout", "timed out")):
        return "provider_timeout"
    if re.search(r"\b429\b", text):
        return "http_429"
    if re.search(r"\b5\d\d\b", text):
        return "http_5xx"
    return None


async def analyze_with_retries(
    engine: NativeMedAgentEngine,
    trace_root: Path,
    description: str,
    question: str,
    session_id: str,
    log_path: Path,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    failures = []
    for attempt in range(1, MAX_INFRASTRUCTURE_RETRIES + 2):
        before = {path for path in trace_root.iterdir() if path.is_dir()}
        started = utc_now()
        try:
            result = await engine.analyze(description, question, session_id)
            trace_dir = trace_root / str(result["run_id"])
            append_log(
                log_path,
                "case_completed",
                session_id=session_id,
                attempt=attempt,
                run_id=result["run_id"],
            )
            return result, {
                "attempts": attempt,
                "infrastructure_retry_count": attempt - 1,
                "failures": failures,
                "trace_dir": trace_dir,
            }
        except Exception as error:
            trace_dir = latest_new_trace(trace_root, before)
            reason = infrastructure_reason(error, trace_dir)
            failure = redact_trace_value(
                {
                    "attempt": attempt,
                    "timestamp": started,
                    "failure_type": type(error).__name__,
                    "message": str(error),
                    "infrastructure_reason": reason,
                    "trace_dir": trace_dir.as_posix() if trace_dir else None,
                }
            )
            failures.append(failure)
            append_log(log_path, "case_failed", session_id=session_id, **failure)
            if reason is None or attempt > MAX_INFRASTRUCTURE_RETRIES:
                return None, {
                    "attempts": attempt,
                    "infrastructure_retry_count": attempt - 1,
                    "failures": failures,
                    "trace_dir": trace_dir,
                }
    raise AssertionError("unreachable")


def scan_secrets(paths: list[Path], secrets: list[str]) -> list[str]:
    findings = []
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="ignore")
        for index, secret in enumerate(secrets, 1):
            if secret and len(secret) >= 4 and secret in text:
                findings.append(f"{path.name}:secret-{index}")
    return findings


def presentation_complete(result: dict[str, Any]) -> bool:
    presentation = result.get("presentation") or {}
    required = {
        "headline",
        "plain_language_summary",
        "professional_answer",
        "evidence_cards",
        "execution_summary",
        "disclaimer",
    }
    return required.issubset(presentation) and (
        presentation.get("professional_answer") == result.get("final_answer")
    )


def make_config(
    trace_dir: Path,
    base_url: str,
    api_key: str,
    milvus_uri: Path,
) -> RuntimeConfig:
    return RuntimeConfig(
        mode="native",
        trace_dir=str(trace_dir),
        llm_base_url=base_url,
        llm_api_key=api_key,
        llm_model=REQUESTED_MODEL,
        llm_temperature=0.0,
        llm_max_tokens=1200,
        llm_timeout_seconds=60.0,
        max_tool_calls=2,
        worker_timeout_seconds=180.0,
        retrieval_mode="milvus",
        milvus_uri=str(milvus_uri),
        milvus_token="",
        embedding_model=EMBEDDING_MODEL,
        generic_collection=GENERIC_COLLECTION,
        special_collection=SPECIAL_COLLECTION,
        retrieval_top_k=TOP_K,
        retrieval_threshold=ADMISSION_THRESHOLD,
    )


def embedding_identity(engine: NativeMedAgentEngine) -> dict[str, Any]:
    embed = getattr(engine.retrieval_backend, "embed_query", None)
    owner = getattr(embed, "__self__", None)
    model = getattr(owner, "_model", None)
    dimension = model.get_sentence_embedding_dimension() if model is not None else None
    return {
        "model": getattr(owner, "model_name", EMBEDDING_MODEL),
        "dimension": int(dimension) if dimension is not None else None,
        "device": str(getattr(model, "device", "unknown")),
    }


def sanitized_sample(events: list[dict[str, Any]], summary: dict[str, Any]) -> dict[str, Any]:
    keep = {
        "contract_built",
        "evidence_ledger_built",
        "plan_created",
        "route_selected",
        "tool_call",
        "tool_result",
        "retrieval_query",
        "retrieval_result",
        "checker_result",
        "patch_applied",
    }
    output_events = []
    for event in events:
        if event["event_type"] not in keep:
            continue
        item = {
            "stage": event["stage"],
            "event_type": event["event_type"],
            "agent": event.get("agent"),
            "payload": event["payload"],
        }
        if event["event_type"] == "retrieval_result":
            payload = event["payload"]
            item["payload"] = {
                "tool": payload.get("tool"),
                "raw_candidates": [
                    {
                        key: candidate.get(key)
                        for key in (
                            "evidence_id",
                            "document_id",
                            "source_id",
                            "title",
                            "section",
                            "source",
                            "score",
                            "rank",
                            "admitted",
                            "admission_reason",
                        )
                    }
                    for candidate in payload.get("raw_candidates") or []
                ],
                "admission": payload.get("admission") or [],
                "compact_evidence": [
                    {
                        **{
                            key: evidence.get(key)
                            for key in (
                                "evidence_id",
                                "title",
                                "source",
                                "section",
                                "score",
                            )
                        },
                        "text_preview": str(evidence.get("text") or "")[:240],
                    }
                    for evidence in payload.get("compact_evidence") or []
                ],
            }
        elif event["event_type"] == "tool_result":
            result = event["payload"].get("result") or {}
            item["payload"] = {
                "name": event["payload"].get("name"),
                "query": result.get("query") if isinstance(result, dict) else None,
                "collection": result.get("collection") if isinstance(result, dict) else None,
                "admitted_count": len(result.get("admitted") or [])
                if isinstance(result, dict)
                else 0,
            }
        output_events.append(item)
    safe_summary = {key: value for key, value in summary.items() if key != "trace_dir"}
    return {
        "fixture_type": "sanitized_native_real_synthetic_smoke",
        "events": output_events,
        "metrics": safe_summary,
    }


async def run_stage_a(
    repo: Path,
    output_root: Path,
    base_url: str,
    api_key: str,
    milvus_uri: Path,
    log_path: Path,
) -> tuple[bool, dict[str, Any], Path | None]:
    trace_root = output_root / "traces" / "stage_a"
    trace_root.mkdir(parents=True, exist_ok=True)
    engine = NativeMedAgentEngine(make_config(trace_root, base_url, api_key, milvus_uri))
    embedding = embedding_identity(engine)
    rows: list[dict[str, Any]] = []

    smoke_cases = [
        (
            "A1_single_focused",
            "SYNTHETIC DEVELOPMENT CASE; adult with intermittent fatigue for two weeks; "
            "no emergency symptoms supplied.",
            "What information and tests are needed for a safe initial assessment?",
            "native-smoke-a1",
        ),
        (
            "A2_multi_real_rag",
            "SYNTHETIC ENGINEERING CASE; adult with fever, productive cough, and shortness "
            "of breath; no examination, imaging, or laboratory results are supplied.",
            "Provide a diagnosis with basis and differential diagnosis, plus management and "
            "safety recommendations. Please combine relevant clinical guidelines or external "
            "medical references using the configured retrieval tools.",
            "native-smoke-a2",
        ),
    ]
    a2_trace: Path | None = None
    try:
        for label, description, question, session_id in smoke_cases:
            result, attempt = await analyze_with_retries(
                engine, trace_root, description, question, session_id, log_path
            )
            trace_dir = attempt.get("trace_dir")
            summary = summarize_trace(trace_dir, result) if trace_dir else {}
            row = {
                "scenario": label,
                "success": result is not None,
                "attempts": attempt["attempts"],
                "infrastructure_retry_count": attempt["infrastructure_retry_count"],
                "failures": attempt["failures"],
                "run_id": result.get("run_id") if result else None,
                "presentation_complete": presentation_complete(result) if result else False,
                "trace": summary,
            }
            rows.append(row)
            if label == "A2_multi_real_rag" and trace_dir:
                a2_trace = trace_dir

        memory_inputs = [
            (
                "A3_turn_1",
                "SYNTHETIC MEMORY CASE; symptom duration is two days; no red flags supplied.",
                "Summarize the current assessment needs.",
            ),
            (
                "A3_turn_2",
                "SYNTHETIC MEMORY CASE ADDITION; the symptom is worse after exertion.",
                "Update the assessment using the new fact.",
            ),
            (
                "A3_turn_3_correction",
                "SYNTHETIC MEMORY CASE CORRECTION; symptom duration is two weeks, not two days.",
                "Use this correction as authoritative and update the assessment.",
            ),
        ]
        memory_trace_dirs: dict[str, Path] = {}
        for label, description, question in memory_inputs:
            result, attempt = await analyze_with_retries(
                engine, trace_root, description, question, "native-smoke-memory", log_path
            )
            trace_dir = attempt.get("trace_dir")
            if trace_dir:
                memory_trace_dirs[label] = trace_dir
            rows.append(
                {
                    "scenario": label,
                    "success": result is not None,
                    "attempts": attempt["attempts"],
                    "infrastructure_retry_count": attempt["infrastructure_retry_count"],
                    "failures": attempt["failures"],
                    "run_id": result.get("run_id") if result else None,
                    "presentation_complete": presentation_complete(result) if result else False,
                    "trace": summarize_trace(trace_dir, result) if trace_dir else {},
                }
            )
        isolated_result, isolated_attempt = await analyze_with_retries(
            engine,
            trace_root,
            "SYNTHETIC ISOLATED SESSION; a separate adult reports a mild headache.",
            "What should be assessed?",
            "native-smoke-isolated",
            log_path,
        )
        isolated_trace = isolated_attempt.get("trace_dir")
        rows.append(
            {
                "scenario": "A3_isolated_session",
                "success": isolated_result is not None,
                "attempts": isolated_attempt["attempts"],
                "infrastructure_retry_count": isolated_attempt["infrastructure_retry_count"],
                "failures": isolated_attempt["failures"],
                "run_id": isolated_result.get("run_id") if isolated_result else None,
                "presentation_complete": presentation_complete(isolated_result)
                if isolated_result
                else False,
                "trace": summarize_trace(isolated_trace, isolated_result)
                if isolated_trace
                else {},
            }
        )

        turn2_path = memory_trace_dirs.get("A3_turn_2")
        turn3_path = memory_trace_dirs.get("A3_turn_3_correction")
        turn2_text = (
            (turn2_path / "trace.jsonl").read_text(encoding="utf-8")
            if turn2_path
            else ""
        )
        turn3_text = (
            (turn3_path / "trace.jsonl").read_text(encoding="utf-8")
            if turn3_path
            else ""
        )
        isolated_text = (
            (isolated_trace / "trace.jsonl").read_text(encoding="utf-8")
            if isolated_trace
            else ""
        )
        correction = "symptom duration is two weeks, not two days"
        history_marker = "symptom duration is two days"
        memory_checks = {
            "turn2_contains_turn1_history": history_marker in turn2_text,
            "turn3_contains_history": history_marker in turn3_text,
            "turn3_contains_current_correction": correction in turn3_text,
            "current_correction_after_history": turn3_text.rfind(correction)
            > turn3_text.find(history_marker),
            "isolated_session_has_no_shared_history": bool(isolated_text)
            and history_marker not in isolated_text
            and correction not in isolated_text,
        }
    finally:
        await engine.close()

    trace_paths = list(trace_root.glob("*/trace.jsonl"))
    secret_findings = scan_secrets(trace_paths, [api_key])
    a1 = next(row for row in rows if row["scenario"] == "A1_single_focused")
    a2 = next(row for row in rows if row["scenario"] == "A2_multi_real_rag")
    all_trace_complete = all(row.get("trace", {}).get("trace_complete") for row in rows)
    all_presentations = all(row.get("presentation_complete") for row in rows)
    all_requests_ended = all(row["success"] for row in rows)
    no_llm_failures = all(
        row.get("trace", {}).get("llm_failed_response_count") == 0 for row in rows
    )
    requested_models = sorted(
        {
            model
            for row in rows
            for model in row.get("trace", {}).get("requested_models", [])
        }
    )
    resolved_models = sorted(
        {
            model
            for row in rows
            for model in row.get("trace", {}).get("resolved_models", [])
        }
    )
    gates = {
        "all_smoke_requests_completed": all_requests_ended,
        "zero_unclosed_llm_requests": all(
            not row.get("trace", {}).get("unclosed_llm_request_ids") for row in rows
        ),
        "no_event_loop_closed_error": all(
            "event loop is closed"
            not in json.dumps(row.get("failures") or [], ensure_ascii=False).casefold()
            for row in rows
        ),
        "native_only_runtime": True,
        "contract_and_ledger_present": all(
            row.get("trace", {}).get("event_counts", {}).get("contract_built", 0) >= 1
            and row.get("trace", {}).get("event_counts", {}).get("evidence_ledger_built", 0)
            >= 1
            for row in rows
        ),
        "planner_and_worker_succeeded": no_llm_failures
        and all(
            row.get("trace", {}).get("event_counts", {}).get("worker_draft", 0) >= 1
            for row in rows
        ),
        "a1_single_route": a1.get("trace", {}).get("route_mode") == "single",
        "a2_multi_route_and_workers": a2.get("trace", {}).get("route_mode") == "multi"
        and len(a2.get("trace", {}).get("route_agents") or []) >= 2,
        "a2_synthesis": a2.get("trace", {}).get("event_counts", {}).get(
            "synthesis_output", 0
        )
        >= 1,
        "a2_real_tool_and_retrieval": a2.get("trace", {}).get("tool_call_count", 0) >= 1
        and a2.get("trace", {}).get("retrieval_query_count", 0) >= 1
        and a2.get("trace", {}).get("raw_candidate_count", 0) > 0,
        "a2_admission_and_evidence_card": a2.get("trace", {}).get(
            "admitted_item_count", 0
        )
        > 0
        and a2.get("trace", {}).get("evidence_cards_have_provenance", False),
        "trace_actual_boundaries_complete": all_trace_complete,
        "trace_contains_zero_secrets": not secret_findings,
        "session_memory_in_real_messages": all(memory_checks.values()),
        "presentation_adapter_complete": all_presentations,
        "requested_model_fixed": requested_models == [REQUESTED_MODEL],
    }
    stage = {
        "status": "PASS" if all(gates.values()) else "FAIL",
        "gates": gates,
        "requested_models": requested_models,
        "resolved_models": resolved_models,
        "embedding": embedding,
        "memory_checks": memory_checks,
        "secret_findings": secret_findings,
        "rows": rows,
        "llm_closed": bool(getattr(getattr(engine.llm, "_client", None), "is_closed", True)),
    }
    write_jsonl(output_root / "SMOKE_RESULTS.jsonl", rows)
    write_json(output_root / "stage_a_summary.json", stage)
    return stage["status"] == "PASS", stage, a2_trace


async def run_stage_b(
    output_root: Path,
    cases: list[dict[str, str]],
    base_url: str,
    api_key: str,
    milvus_uri: Path,
    log_path: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    trace_root = output_root / "traces" / "composite20"
    trace_root.mkdir(parents=True, exist_ok=True)
    output_path = output_root / "NATIVE_COMPOSITE20_OUTPUTS.jsonl"
    rows: list[dict[str, Any]] = []
    if output_path.is_file():
        rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
    completed = {row["eval_id"] for row in rows}
    engine = NativeMedAgentEngine(make_config(trace_root, base_url, api_key, milvus_uri))
    try:
        for case in cases:
            if case["eval_id"] in completed:
                continue
            session_id = f"native-composite-{case['eval_id'].casefold()}"
            result, attempt = await analyze_with_retries(
                engine,
                trace_root,
                case["description"],
                case["question"],
                session_id,
                log_path,
            )
            trace_dir = attempt.get("trace_dir")
            summary = summarize_trace(trace_dir, result) if trace_dir else {}
            answer = str((result or {}).get("final_answer") or "")
            row = {
                "benchmark_name": "CMB-Clin COMPOSITE-20 development benchmark",
                "eval_id": case["eval_id"],
                "description": case["description"],
                "question": case["question"],
                "candidate_answer": answer,
                "candidate_answer_sha256": sha256_text(answer),
                "success": result is not None and bool(answer.strip()),
                "status": "completed" if result is not None else "failed",
                "run_id": result.get("run_id") if result else None,
                "session_id": session_id,
                "session_isolated": True,
                "long_term_memory_enabled": False,
                "attempts": attempt["attempts"],
                "infrastructure_retry_count": attempt["infrastructure_retry_count"],
                "retry_failures": attempt["failures"],
                "trace_review": summary,
            }
            rows.append(redact_trace_value(row))
            write_jsonl(output_path, rows)
    finally:
        await engine.close()

    summaries = [row.get("trace_review") or {} for row in rows]
    successes = [row for row in rows if row.get("success")]
    route_counts = Counter(summary.get("route_mode") or "missing" for summary in summaries)
    worker_counts = [len(summary.get("route_agents") or []) for summary in summaries]
    total_llm_calls = sum(int(summary.get("llm_request_count") or 0) for summary in summaries)
    total_tokens = sum(int(summary.get("total_tokens") or 0) for summary in summaries)
    latencies = [float(summary.get("latency_ms") or 0.0) for summary in summaries]
    tool_cases = sum(int(summary.get("tool_call_count") or 0) > 0 for summary in summaries)
    retrieval_cases = sum(
        int(summary.get("retrieval_query_count") or 0) > 0 for summary in summaries
    )
    retrieval_queries = sum(
        int(summary.get("retrieval_query_count") or 0) for summary in summaries
    )
    admission_candidates = sum(
        int(summary.get("admission_candidate_count") or 0) for summary in summaries
    )
    admitted_items = sum(int(summary.get("admitted_item_count") or 0) for summary in summaries)
    retries = sum(int(row.get("infrastructure_retry_count") or 0) for row in rows)
    timeout_cases = sum(
        "timeout" in json.dumps(row.get("retry_failures") or []).casefold() for row in rows
    )
    metrics = {
        "benchmark_name": "CMB-Clin COMPOSITE-20 development benchmark",
        "case_count": len(rows),
        "generation_success_count": len(successes),
        "generation_success_rate": len(successes) / len(rows) if rows else 0.0,
        "route_distribution": dict(route_counts),
        "single_count": route_counts.get("single", 0),
        "multi_count": route_counts.get("multi", 0),
        "worker_count_total": sum(worker_counts),
        "worker_count_mean": sum(worker_counts) / len(worker_counts) if worker_counts else 0.0,
        "planner_parse_failure_count": sum(
            bool(summary.get("planner_parse_failure")) for summary in summaries
        ),
        "dispatchable_plan_rate": sum(
            bool(summary.get("dispatchable_plan")) for summary in summaries
        )
        / len(summaries)
        if summaries
        else 0.0,
        "llm_call_count": total_llm_calls,
        "total_tokens": total_tokens,
        "mean_tokens_per_case": total_tokens / len(rows) if rows else 0.0,
        "total_latency_ms": sum(latencies),
        "mean_latency_ms": sum(latencies) / len(latencies) if latencies else 0.0,
        "tool_call_case_rate": tool_cases / len(rows) if rows else 0.0,
        "retrieval_case_rate": retrieval_cases / len(rows) if rows else 0.0,
        "retrieval_query_count": retrieval_queries,
        "admission_rate": admitted_items / admission_candidates if admission_candidates else 0.0,
        "checker_edit_case_rate": sum(
            int(summary.get("checker_edit_count") or 0) > 0 for summary in summaries
        )
        / len(rows)
        if rows
        else 0.0,
        "timeout_rate": timeout_cases / len(rows) if rows else 0.0,
        "infrastructure_retry_count": retries,
        "infrastructure_retry_rate": retries / len(rows) if rows else 0.0,
        "trace_completeness_rate": sum(
            bool(summary.get("trace_complete")) for summary in summaries
        )
        / len(rows)
        if rows
        else 0.0,
        "requested_models": sorted(
            {model for summary in summaries for model in summary.get("requested_models", [])}
        ),
        "resolved_models": sorted(
            {model for summary in summaries for model in summary.get("resolved_models", [])}
        ),
        "judge": "NOT_RUN",
        "medical_quality": "NOT_EVALUATED",
    }
    return rows, metrics


def pair_web_outputs(
    cases: list[dict[str, str]], web_path: Path, destination: Path
) -> dict[str, Any]:
    web_rows = [
        json.loads(line) for line in web_path.read_text(encoding="utf-8").splitlines() if line
    ]
    by_case = {str(row.get("case_id")): row for row in web_rows}
    paired = []
    for case in cases:
        number = int(case["eval_id"].split("-")[1])
        case_id = f"case_{number:04d}"
        existing = by_case.get(case_id) or {}
        answer = str(existing.get("response") or "")
        paired.append(
            {
                "eval_id": case["eval_id"],
                "case_id": case_id,
                "question": case["question"],
                "question_sha256": sha256_text(case["question"]),
                "deepseek_web_answer": answer,
                "answer_non_empty": bool(answer.strip()),
                "case_id_match": existing.get("case_id") == case_id,
                "question_non_empty": bool(case["question"].strip()),
                "external_baseline_valid": bool(answer.strip())
                and existing.get("case_id") == case_id,
                "external_baseline_status": "existing_unmodified",
            }
        )
    write_jsonl(destination, paired)
    return {
        "count": len(paired),
        "valid_count": sum(row["external_baseline_valid"] for row in paired),
        "invalid_count": sum(not row["external_baseline_valid"] for row in paired),
        "web_contacted": False,
        "answers_modified": False,
    }


def write_reports(
    output_root: Path,
    config: dict[str, Any],
    stage_a: dict[str, Any],
    metrics: dict[str, Any] | None,
    pairing: dict[str, Any] | None,
    production_diff: str,
) -> None:
    gates = stage_a["gates"]
    smoke_lines = [
        "# Real Native E2E Smoke",
        "",
        f"- Status: **{stage_a['status']}**",
        f"- Requested model: `{REQUESTED_MODEL}`",
        f"- Resolved model(s): `{', '.join(stage_a['resolved_models']) or 'none'}`",
        f"- Real LLM: **{'PASS' if stage_a['resolved_models'] else 'FAIL'}**",
        f"- Real Milvus: **{'PASS' if gates['a2_real_tool_and_retrieval'] else 'FAIL'}**",
        f"- Embedding: `{stage_a['embedding']['model']}` / "
        f"{stage_a['embedding']['dimension']} dimensions / `{stage_a['embedding']['device']}`",
        f"- Production change in this task: `{production_diff}`",
        "",
        "## Gates",
        "",
        "| Gate | Result |",
        "|---|---|",
    ]
    smoke_lines.extend(
        f"| {name} | {'PASS' if value else 'FAIL'} |" for name, value in gates.items()
    )
    smoke_lines.extend(
        [
            "",
            "Judge: `NOT_RUN`  ",
            "Medical quality: `NOT_EVALUATED`",
        ]
    )
    (output_root / "REAL_E2E_SMOKE.md").write_text(
        "\n".join(smoke_lines) + "\n", encoding="utf-8"
    )
    completeness = {
        "stage_a_status": stage_a["status"],
        "trace_actual_boundaries_complete": gates["trace_actual_boundaries_complete"],
        "zero_unclosed_llm_requests": gates["zero_unclosed_llm_requests"],
        "trace_contains_zero_secrets": gates["trace_contains_zero_secrets"],
        "requested_models": stage_a["requested_models"],
        "resolved_models": stage_a["resolved_models"],
        "scenario_trace_completeness": {
            row["scenario"]: row.get("trace", {}).get("trace_complete", False)
            for row in stage_a["rows"]
        },
    }
    write_json(output_root / "SMOKE_TRACE_COMPLETENESS.json", completeness)
    (output_root / "SMOKE_TRACE_COMPLETENESS.md").write_text(
        "# Smoke Trace Completeness\n\n"
        "- Actual request/response boundaries: "
        f"**{completeness['trace_actual_boundaries_complete']}**\n"
        f"- Unclosed requests: **{not completeness['zero_unclosed_llm_requests']}**\n"
        f"- Secret findings: **{not completeness['trace_contains_zero_secrets']}**\n"
        f"- Requested models: `{completeness['requested_models']}`\n"
        f"- Resolved models: `{completeness['resolved_models']}`\n",
        encoding="utf-8",
    )
    if metrics is not None:
        write_json(output_root / "NATIVE_COMPOSITE20_ENGINEERING_METRICS.json", metrics)
        metric_lines = [
            "# Native COMPOSITE-20 Engineering Metrics",
            "",
            "CMB-Clin COMPOSITE-20 development benchmark. These are engineering "
            "metrics, not medical quality scores.",
            "",
        ]
        metric_lines.extend(f"- {key}: `{value}`" for key, value in metrics.items())
        (output_root / "NATIVE_COMPOSITE20_ENGINEERING_METRICS.md").write_text(
            "\n".join(metric_lines) + "\n", encoding="utf-8"
        )
    integrity = [
        "# Generation Integrity",
        "",
        f"- Native commit: `{config['git_commit']}`",
        f"- Production source SHA-256: `{config['production_source_sha256']}`",
        f"- Stage A: `{stage_a['status']}`",
        "- Candidate policy: first valid complete run; no quality retry or manual answer editing.",
        f"- Infrastructure retry maximum: `{MAX_INFRASTRUCTURE_RETRIES}`",
        "- Per-case sessions: isolated; long-term memory disabled.",
        f"- Existing DeepSeek Web pairing: `{pairing}`",
        "- Judge: `NOT_RUN`",
        "- Medical quality: `NOT_EVALUATED`",
    ]
    (output_root / "GENERATION_INTEGRITY.md").write_text(
        "\n".join(integrity) + "\n", encoding="utf-8"
    )


async def main_async(args: argparse.Namespace) -> int:
    repo = Path(__file__).resolve().parents[1]
    output_root = Path(args.output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    log_path = output_root / "generation_log.jsonl"
    env_values = load_dotenv(Path(args.env_file))
    base_url = env_values.get("LLM_BASE_URL") or env_values.get("MEDAGENT_LLM_BASE_URL", "")
    api_key = env_values.get("LLM_API_KEY") or env_values.get("MEDAGENT_LLM_API_KEY", "")
    configured_model = env_values.get("LLM_MODEL_NAME") or env_values.get(
        "MEDAGENT_LLM_MODEL", ""
    )
    if not base_url or not api_key:
        raise RuntimeError("authorized env file does not contain LLM base URL and API key")
    if configured_model != REQUESTED_MODEL:
        raise RuntimeError(
            f"configured requested model must be exactly {REQUESTED_MODEL!r}; "
            f"got {configured_model!r}"
        )
    milvus_uri = Path(args.milvus_uri).resolve()
    source_path = Path(args.composite_source).resolve()
    web_path = Path(args.web_output).resolve()
    if not milvus_uri.exists() or not source_path.is_file() or not web_path.is_file():
        raise FileNotFoundError("Milvus copy, composite source, or Web output is missing")

    commit = git_output(repo, "rev-parse", "HEAD")
    status_before = git_output(repo, "status", "--short")
    source_sha, source_count = production_source_sha(repo)
    dependency_hits = private_dependency_hits(repo)
    config = {
        "created_at": utc_now(),
        "python_version": sys.version.split()[0],
        "sys_executable": sys.executable,
        "git_commit": commit,
        "git_status_before_generation": status_before,
        "production_source_sha256": source_sha,
        "production_file_count": source_count,
        "runtime_mode": "native",
        "private_dependency_hits": dependency_hits,
        "endpoint_identity": urlparse(base_url).netloc,
        "requested_model": REQUESTED_MODEL,
        "temperature": 0.0,
        "max_tokens": 1200,
        "retrieval_mode": "milvus",
        "milvus_identity": "local-copied-milvus-lite",
        "embedding_model": EMBEDDING_MODEL,
        "generic_collection": GENERIC_COLLECTION,
        "special_collection": SPECIAL_COLLECTION,
        "top_k": TOP_K,
        "admission_threshold": ADMISSION_THRESHOLD,
        "composite_source_sha256": sha256_file(source_path),
        "web_output_sha256": sha256_file(web_path),
    }
    write_json(output_root / "REAL_E2E_CONFIG.json", config)
    precheck = [
        "# Native Runtime Precheck",
        "",
        f"- Native git commit: `{commit}`",
        f"- Production source SHA-256: `{source_sha}` ({source_count} files)",
        "- Runtime mode: `native`",
        f"- Private dependency hits: `{len(dependency_hits)}`",
        f"- Endpoint identity: `{config['endpoint_identity']}`",
        f"- Requested model: `{REQUESTED_MODEL}`",
        "- Execution code root: public `medagent-harness` only",
        "- Private runtime/freeze/legacy/swarm imports: none",
    ]
    (output_root / "NATIVE_RUNTIME_PRECHECK.md").write_text(
        "\n".join(precheck) + "\n", encoding="utf-8"
    )
    if dependency_hits:
        raise RuntimeError("private runtime dependency detected")

    append_log(log_path, "stage_a_started", git_commit=commit)
    stage_a_pass, stage_a, a2_trace = await run_stage_a(
        repo, output_root, base_url, api_key, milvus_uri, log_path
    )
    config["embedding"] = stage_a["embedding"]
    config["resolved_models"] = stage_a["resolved_models"]
    write_json(output_root / "REAL_E2E_CONFIG.json", config)
    if not stage_a_pass:
        write_reports(output_root, config, stage_a, None, None, "b8e4b06")
        append_log(log_path, "stage_b_blocked", reason="stage_a_gate_failure")
        return 2

    cases = load_allowed_cases(source_path)
    append_log(log_path, "stage_b_started", case_count=len(cases))
    rows, metrics = await run_stage_b(
        output_root, cases, base_url, api_key, milvus_uri, log_path
    )
    pairing = pair_web_outputs(
        cases, web_path, output_root / "DEEPSEEK_WEB_COMPOSITE20_PAIRED.jsonl"
    )
    output_sha = sha256_file(output_root / "NATIVE_COMPOSITE20_OUTPUTS.jsonl")
    metrics["native_outputs_sha256"] = output_sha
    write_json(output_root / "NATIVE_COMPOSITE20_ENGINEERING_METRICS.json", metrics)
    write_reports(output_root, config, stage_a, metrics, pairing, "b8e4b06")

    if a2_trace:
        sample_root = repo / "examples" / "sample_native_real_trace"
        sample_root.mkdir(parents=True, exist_ok=True)
        sample_summary = next(
            row["trace"] for row in stage_a["rows"] if row["scenario"] == "A2_multi_real_rag"
        )
        sample = sanitized_sample(read_trace(a2_trace), sample_summary)
        write_json(sample_root / "trace.json", sample)
        (sample_root / "README.md").write_text(
            "# Sanitized Native Real Trace\n\n"
            "This fixture comes from a synthetic Stage A case using a real model, embedding, "
            "and copied local Milvus database. It omits model prompts, credentials, endpoint "
            "details, patient data, and full corpus records.\n",
            encoding="utf-8",
        )

    append_log(
        log_path,
        "generation_finished",
        success_count=metrics["generation_success_count"],
        output_sha256=output_sha,
        judge="NOT_RUN",
    )
    hashes = {}
    for path in sorted(output_root.rglob("*")):
        if path.is_file() and path.name != "SOURCE_AND_OUTPUT_SHA256SUMS.txt":
            hashes[path.relative_to(output_root).as_posix()] = sha256_file(path)
    (output_root / "SOURCE_AND_OUTPUT_SHA256SUMS.txt").write_text(
        "".join(f"{digest}  {name}\n" for name, digest in hashes.items()), encoding="utf-8"
    )
    return 0 if len(rows) == 20 else 3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Native real E2E and COMPOSITE-20")
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--milvus-uri", required=True)
    parser.add_argument("--composite-source", required=True)
    parser.add_argument("--web-output", required=True)
    parser.add_argument(
        "--output-root", default="eval/native_real_validation_v1"
    )
    return parser.parse_args()


def main() -> None:
    raise SystemExit(asyncio.run(main_async(parse_args())))


if __name__ == "__main__":
    main()
