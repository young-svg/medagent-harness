"""python -m eval.w7.smoke --output eval/w7/smoke_results/final

Only the five approved smoke cases; never a real-provider or 24-case runner.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from hashlib import sha256
from pathlib import Path

from .data import SMOKE_CASES
from .runner import run_case


def shared_cost(record):
    attempts = [m for m in record["models"] if m["stage"] == "planning"]
    return {
        "owner_run_id": record["run_id"],
        "plan_sha256": record["plan_sha256"],
        "model_attempts": len(attempts),
        "input_tokens": sum(m["input_tokens"] for m in attempts)
        if all(type(m["input_tokens"]) is int for m in attempts)
        else "UNKNOWN",
        "output_tokens": sum(m["output_tokens"] for m in attempts)
        if all(type(m["output_tokens"]) is int for m in attempts)
        else "UNKNOWN",
        "usage_origin": "SCRIPTED_ONLY",
        "charged_to_actual_serial_attempts": False,
        "planning_latency_ms": sum(m["latency_ms"] for m in attempts),
    }


async def smoke(output):
    records = []
    for case_id in SMOKE_CASES:
        for group in ("A", "B", "C"):
            row, plan = await run_case(case_id, group, output)
            records.append(row)
            if group == "C":
                serial, _ = await run_case(
                    case_id, "C-serial", output, replay=plan, shared_plan_cost=shared_cost(row)
                )
                records.append(serial)
    for group in ("A", "B", "C"):
        row, plan = await run_case("M01", group, output, memory_on=False)
        records.append(row)
        if group == "C":
            serial, _ = await run_case(
                "M01",
                "C-serial",
                output,
                replay=plan,
                memory_on=False,
                shared_plan_cost=shared_cost(row),
            )
            records.append(serial)
    for case_id, fault, limits in (
        ("D03", "upstream_failure", None),
        ("E03", "classification_only", None),
        ("E03", "swapped_citations", None),
        ("E03", "duplicate_id", None),
        ("E03", "replay_after_retry", None),
        ("D03", None, {"worker_limit": 2}),
        ("E03", None, {"tool_limit": 1}),
        ("D03", None, {"model_limit": 2}),
    ):
        # Separate directories distinguish budget variants without overwriting records.
        folder = output / (fault or next(iter(limits)))
        for group in ("A", "B", "C"):
            row, plan = await run_case(case_id, group, folder, fault=fault, limits=limits)
            records.append(row)
            if group == "C" and plan is not None:
                serial, _ = await run_case(
                    case_id,
                    "C-serial",
                    folder,
                    fault=fault,
                    limits=limits,
                    replay=plan,
                    shared_plan_cost=shared_cost(row),
                )
                records.append(serial)
    matrix = [
        {
            "case_id": r["case_id"],
            "architecture": r["architecture"],
            "memory_on": r["memory_on"],
            "fault": r["fault"],
            "status": r["status"],
            "run_id": r["run_id"],
            "model_attempts": r["metrics"]["model_attempts"],
            "stage_attempts": r["metrics"]["by_stage"],
            "tool_attempts": r["metrics"]["tool_attempts"],
            "handler_executions": r["metrics"]["handler_executions"],
            "peak_worker_contexts": r["metrics"]["peak_worker_contexts"],
            "limits": r["metrics"]["limits"],
            "plan_sha256": r["plan_sha256"],
            "acceptance_observed": r["acceptance_observed"],
            "independent_grade": r["independent_score"]["grade"],
        }
        for r in records
    ]
    output.mkdir(parents=True, exist_ok=True)
    (output / "matrix.json").write_text(json.dumps(matrix, indent=2) + "\n", encoding="utf-8")
    index = {
        str(p.relative_to(output)): sha256(p.read_bytes()).hexdigest()
        for p in sorted(output.rglob("*.json*"))
        if p.name != "run_manifest.json"
    }
    (output / "run_manifest.json").write_text(
        json.dumps(
            {
                "purpose": "SCRIPTED ENGINEERING SMOKE; NO ARCHITECTURE QUALITY CLAIM",
                "runs": len(records),
                "files_sha256": index,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "runs": len(records),
                "matrix": str(output / "matrix.json"),
                "all_unscored": all(r["independent_score"]["grade"] is None for r in records),
            },
            indent=2,
        )
    )
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Use a new output directory to preserve existing run manifests.")
    asyncio.run(smoke(args.output))


if __name__ == "__main__":
    main()
