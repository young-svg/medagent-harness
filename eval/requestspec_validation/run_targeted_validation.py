from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from medagent.runtime.config import RuntimeConfig
from medagent.runtime.native_engine import NativeMedAgentEngine

TARGET_IDS = ["MED-050", "MED-054", "MED-055", "MED-060"]
FORBIDDEN_INPUT_FIELDS = {
    "reference_answer",
    "deepseek_answer",
    "manual_score",
    "manual_scores",
}


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        clean = line.strip()
        if not clean or clean.startswith("#") or "=" not in clean:
            continue
        key, value = clean.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_cases(path: Path, target_ids: list[str]) -> list[dict[str, str]]:
    cases: dict[str, dict[str, str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        forbidden = FORBIDDEN_INPUT_FIELDS.intersection(raw)
        if forbidden:
            raise RuntimeError(f"forbidden validation fields present: {sorted(forbidden)}")
        eval_id = str(raw.get("eval_id") or "")
        if eval_id in target_ids:
            cases[eval_id] = {
                "eval_id": eval_id,
                "description": str(raw["description"]),
                "question": str(raw["question"]),
            }
    if set(cases) != set(target_ids):
        raise RuntimeError("target source does not contain the selected requested cases")
    return [cases[eval_id] for eval_id in target_ids]


def markdown_report(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Targeted RequestSpec Validation",
        "",
        "Only MED-050, MED-054, MED-055, and MED-060 were run. No reference answer, "
        "DeepSeek answer, manual score, judge, or quality scoring was read or used.",
        "",
        "| Case | Items | Route | UserRequestComplete | ContractComplete | Final | Result |",
        "|---|---:|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['eval_id']} | {row['request_item_count']} | {row['route_mode']} | "
            f"{row['user_request_complete']} | {row['contract_complete']} | "
            f"{row['final_non_empty']} | {row['validation_status']} |"
        )
    lines.extend(
        [
            "",
            "Validation is structural only: extraction count, planner mapping, schema-valid "
            "per-item answers, completion flags, and non-empty final output.",
        ]
    )
    return "\n".join(lines) + "\n"


async def run(args: argparse.Namespace) -> int:
    env = load_env(Path(args.env_file))
    base_url = env.get("LLM_BASE_URL") or env.get("MEDAGENT_LLM_BASE_URL", "")
    api_key = env.get("LLM_API_KEY") or env.get("MEDAGENT_LLM_API_KEY", "")
    model = env.get("LLM_MODEL_NAME") or env.get("MEDAGENT_LLM_MODEL", "")
    if not base_url or not api_key or not model:
        raise RuntimeError("LLM endpoint configuration is incomplete")

    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    target_ids = args.target_id or TARGET_IDS
    cases = load_cases(Path(args.source), target_ids)
    engine = NativeMedAgentEngine(
        RuntimeConfig(
            trace_dir=str(output_root / "traces"),
            llm_base_url=base_url,
            llm_api_key=api_key,
            llm_model=model,
            llm_temperature=0.0,
            retrieval_mode="off",
        )
    )
    rows: list[dict[str, Any]] = []
    try:
        for case in cases:
            result = await engine.analyze(
                case["description"], case["question"], f"requestspec-{case['eval_id']}"
            )
            execution = result["presentation"]["execution_summary"]
            request_items = execution["request_spec"]["items"]
            required_ids = [item["id"] for item in request_items if item["required"]]
            answers = result.get("request_item_answers") or {}
            planner_mapping = {
                subtask["subtask_id"]: subtask.get("request_item_ids") or []
                for subtask in execution["plan"]["subtasks"]
            }
            all_subtasks_mapped = all(planner_mapping.values())
            mapped_ids = {
                request_id
                for request_ids in planner_mapping.values()
                for request_id in request_ids
            }
            all_required_mapped = set(required_ids).issubset(mapped_ids)
            all_required_answered = all(
                isinstance(answers.get(request_id), str)
                and bool(answers[request_id].strip())
                for request_id in required_ids
            )
            passed = all(
                (
                    result.get("status") == "completed",
                    result.get("user_request_complete") is True,
                    result.get("contract_complete") is True,
                    bool(result.get("final_answer", "").strip()),
                    all_subtasks_mapped,
                    all_required_mapped,
                    all_required_answered,
                )
            )
            rows.append(
                {
                    "eval_id": case["eval_id"],
                    "request_item_count": len(request_items),
                    "request_items": request_items,
                    "planner_request_item_mapping": planner_mapping,
                    "all_subtasks_mapped": all_subtasks_mapped,
                    "all_required_items_mapped": all_required_mapped,
                    "answered_request_item_ids": sorted(answers),
                    "all_required_items_answered": all_required_answered,
                    "user_request_complete": result.get("user_request_complete"),
                    "contract_complete": result.get("contract_complete"),
                    "final_non_empty": bool(result.get("final_answer", "").strip()),
                    "route_mode": execution["route"]["mode"],
                    "run_id": result["run_id"],
                    "validation_status": "PASS" if passed else "FAIL",
                }
            )
    finally:
        await engine.close()

    output_path = output_root / "TARGETED_VALIDATION.json"
    existing: list[dict[str, Any]] = []
    if output_path.is_file():
        existing = json.loads(output_path.read_text(encoding="utf-8"))
    merged = {str(row["eval_id"]): row for row in existing}
    merged.update({str(row["eval_id"]): row for row in rows})
    ordered = [merged[eval_id] for eval_id in TARGET_IDS if eval_id in merged]
    output_path.write_text(
        json.dumps(ordered, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_root / "TARGETED_VALIDATION.md").write_text(
        markdown_report(ordered), encoding="utf-8"
    )
    return 0 if all(row["validation_status"] == "PASS" for row in rows) else 3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--target-id", action="append", choices=TARGET_IDS)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parse_args())))
