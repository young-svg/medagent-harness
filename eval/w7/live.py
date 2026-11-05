"""Explicit live probes then nine authorized runs. No dotenv loading or fallback."""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

from medagent.context.evidence_ledger import build_evidence_ledger
from medagent.llm.client import OpenAICompatibleLLM
from medagent.observability.tracer import TraceRecorder, redact_trace_value
from medagent.planning.complexity import build_complexity_profile, build_response_profile
from medagent.planning.models import Subtask
from medagent.retrieval.evidence import EvidenceBundle
from medagent.runtime.agent_loop import AgentLoop
from medagent.runtime.config import RuntimeConfig
from medagent.runtime.native_engine import AGENTS

from .budget import BatchBudget, MeteredLLM, RunBudget, http_attempt
from .data import FrozenCorpusBackend, frozen_request, load_case
from .runner import EvaluationEngine, run_case


def live_config():
    config = RuntimeConfig.from_env()
    if (
        not config.llm_base_url
        or not config.llm_api_key
        or not config.llm_model
        or config.llm_model in {"deterministic-local", "scripted-test-model"}
    ):
        raise ValueError("live_provider_configuration_required_no_fallback")
    # User authorized no automatic retries. Protocol repair is blocked at MeteredLLM.
    return replace(
        config,
        planner_max_length_recoveries=0,
        worker_max_length_recoveries=0,
        synthesis_max_length_recoveries=0,
        worker_max_infrastructure_retries=0,
        trace_content_mode="metadata",
        semantic_validation_enabled=True,
        max_tool_calls=2,
        worker_max_model_rounds=8,
        worker_infrastructure_retry_base_delay_seconds=0,
    )


def make_client(config, batch):
    client = OpenAICompatibleLLM(
        base_url=config.llm_base_url,
        api_key=config.llm_api_key,
        model=config.llm_model,
        temperature=config.llm_temperature,
        max_tokens=config.llm_max_tokens,
        timeout_seconds=config.llm_timeout_seconds,
    )

    async def response_hook(response):
        row = http_attempt.get()
        if row is not None:
            row["http_status"] = response.status_code
        if response.status_code >= 300:
            batch.stop(
                "http_400"
                if response.status_code == 400
                else "authentication_error"
                if response.status_code in {401, 403}
                else "provider_http_error"
            )

    # Observe only HTTP status, never headers, URL credentials or response bodies.
    client._client.event_hooks["response"].append(response_hook)
    return client


def save(path, value, secret):
    text = json.dumps(redact_trace_value(value), ensure_ascii=False, indent=2)
    if secret:
        text = text.replace(secret, "[REDACTED]")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")


async def probe_json(output, config, batch):
    budget = RunBudget(model_limit=1, worker_limit=0, tool_limit=0, batch=batch)
    client = MeteredLLM(
        make_client(config, batch),
        budget,
        usage_origin="provider_reported",
        stage_override="probe_json",
        allow_recovery=False,
    )
    reason = None
    try:
        response = await client.complete(
            [
                {"role": "system", "content": "Return one JSON object only."},
                {"role": "user", "content": 'Protocol test, no clinical data. Return {"ok":true}.'},
            ],
            response_format={"type": "json_object"},
            max_tokens=1200,
            temperature=0.0,
        )
        passed = (
            not response.tool_calls
            and response.finish_reason == "stop"
            and json.loads(response.content) == {"ok": True}
        )
        if not passed:
            reason = "json_probe_invalid_response"
    except Exception as error:
        passed, reason = False, type(error).__name__
    finally:
        await client.close()
    if not passed:
        batch.stop("json_probe_failed")
    record = {
        "probe": "P1",
        "passed": passed,
        "reason": reason,
        "metrics": budget.summary(),
        "models": budget.models,
        "stop_reason": batch.stop_reason,
    }
    save(output / "P1.json", record, config.llm_api_key)
    return record


async def probe_tool(output, config, batch):
    """Use the real AgentLoop serializer and existing read-only research tool."""
    case = load_case("E03")
    request = frozen_request(case)
    spec, contract, _ = request.materialize(request.description, request.question)
    budget = RunBudget(model_limit=2, worker_limit=2, tool_limit=1, batch=batch)
    primary = MeteredLLM(
        make_client(config, batch), budget, usage_origin="provider_reported", allow_recovery=False
    )
    config = replace(config, trace_dir=str(output / "traces"), worker_max_model_rounds=2)
    engine = EvaluationEngine(
        config,
        llm=primary,
        validation_llm=primary,
        retrieval_backend=FrozenCorpusBackend("E03"),
        budget=budget,
        common_tools={"search_knowledge"},
    )
    bundle = EvidenceBundle("", "", [])
    bundle.case_description, bundle.evidence_requirements = (
        request.description,
        request.evidence_requirements,
    )
    complexity = build_complexity_profile(
        request.question, contract, spec, frozen_evidence_requirements=request.evidence_requirements
    )
    tools = engine._tool_registry(
        request.question, bundle, {"research_agent": {"search_knowledge"}}
    )
    trace = TraceRecorder(config.trace_dir, secrets=(config.llm_api_key,))
    trace.record("run_start", {"mode": "live_protocol_probe"}, stage="lifecycle")
    result, reason = None, None
    try:
        result = await AgentLoop(
            primary,
            tools,
            max_tool_calls=2,
            max_tokens=config.worker_max_tokens,
            max_length_recoveries=0,
            max_infrastructure_retries=0,
            max_model_rounds=2,
        ).execute(
            AGENTS["research_agent"],
            Subtask(
                "protocol-paper",
                "For RQ2, call search_knowledge exactly once to retrieve "
                "SYNTHETIC cough research material, then answer RQ2 citing the returned source.",
                "research_agent",
                ["COMPREHENSIVE_CASE_ANALYSIS"],
                request_item_ids=["RQ2"],
            ),
            spec,
            contract,
            build_evidence_ledger(request.description),
            {"description": request.description, "question": request.question},
            [],
            engine.skills["research_agent"],
            trace,
            complexity,
            build_response_profile(complexity),
            evidence_bundle=bundle,
        )
        passed = (
            result.success
            and len(budget.models) == 2
            and budget.summary()["handler_executions"] == 1
            and [r.get("returned_tool_calls") for r in budget.models] == [1, 0]
            and not batch.stop_reason
        )
        if not passed:
            reason = result.failure_reason or "tool_continuation_not_exercised_or_failed"
    except Exception as error:
        passed, reason = False, type(error).__name__
    finally:
        await engine.close()
    if not passed:
        batch.stop("tool_continuation_failed")
    trace.finish("completed" if passed else "incomplete")
    record = {
        "probe": "P2",
        "passed": passed,
        "reason": reason,
        "metrics": budget.summary(),
        "models": budget.models,
        "tools": budget.tools,
        "worker_result": result.to_dict() if result else None,
        "run_id": trace.run_id,
        "stop_reason": batch.stop_reason,
    }
    save(output / "P2.json", record, config.llm_api_key)
    return record


async def experiment(output):
    config = live_config()  # Fail before creating any client if env is incomplete.
    probes_budget, pilot_budget = BatchBudget(3), BatchBudget(144)
    output.mkdir(parents=True, exist_ok=False)
    probes, records = [], []
    probes.append(await probe_json(output / "probes", config, probes_budget))
    if probes[-1]["passed"]:
        probes.append(await probe_tool(output / "probes", config, probes_budget))
    if len(probes) == 2 and all(p["passed"] for p in probes):
        for case_id in ("S02", "I03", "D03"):
            for architecture in ("A", "B", "C"):
                record, _ = await run_case(
                    case_id, architecture, output / "pilot", mode="live", batch=pilot_budget
                )
                records.append(record)
                if record["status"] in {"error", "budget_exhausted", "stopped"}:
                    pilot_budget.stop("pilot_execution_stopped")
                if pilot_budget.stop_reason:
                    break
            if pilot_budget.stop_reason:
                break
    matrix = [
        {
            "case_id": r["case_id"],
            "architecture": r["architecture"],
            "run_id": r["run_id"],
            "status": r["status"],
            "internal_status": r["native_status"],
            "metrics": r["metrics"],
            "latency_ms": r["latency_ms"],
            "rq_acceptance": r["rq_acceptance"],
            "independent_grade": "UNSCORED",
            "resolved_models": sorted({m.get("resolved_model", "UNKNOWN") for m in r["models"]}),
            "failure_reason": r["batch_stop_reason"],
        }
        for r in records
    ]
    summary = {
        "mode": "LIVE_PROVIDER",
        "probes": probes,
        "pilot": matrix,
        "probe_attempts": probes_budget.attempts,
        "probe_limit": 3,
        "pilot_attempts": pilot_budget.attempts,
        "pilot_limit": 144,
        "stop_reason": probes_budget.stop_reason or pilot_budget.stop_reason,
        "pilot_not_started": not records,
        "all_independent_scores": "UNSCORED",
    }
    save(output / "summary.json", summary, config.llm_api_key)
    files = {
        str(p.relative_to(output)): sha256(p.read_bytes()).hexdigest()
        for p in sorted(output.rglob("*.json*"))
        if p.name != "run_manifest.json"
    }
    save(output / "run_manifest.json", {"files_sha256": files}, config.llm_api_key)
    print(
        json.dumps(
            {
                "summary": str(output / "summary.json"),
                "probe_attempts": probes_budget.attempts,
                "pilot_attempts": pilot_budget.attempts,
                "stop_reason": summary["stop_reason"],
            }
        )
    )
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["live"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    asyncio.run(experiment(args.output))


if __name__ == "__main__":
    main()
