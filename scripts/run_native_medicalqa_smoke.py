"""Run real Native Retrieval smoke queries against an isolated MedicalQA Milvus copy."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from medagent.retrieval.admission import admit_evidence
from medagent.retrieval.config import ExternalMedicalKnowledgeConfig
from medagent.retrieval.factory import build_external_medical_knowledge_backend


@dataclass(frozen=True, slots=True)
class SmokeCase:
    case_id: str
    query: str
    logical_collection: str
    expected_sections: tuple[str, ...]


CASES = (
    SmokeCase(
        "case_1",
        "糖尿病治疗原则",
        "clinical_knowledge",
        ("treatment",),
    ),
    SmokeCase(
        "case_2",
        "胃癌鉴别诊断",
        "clinical_knowledge",
        ("differential", "diagnosis"),
    ),
    SmokeCase(
        "case_3",
        "高血压生活方式管理",
        "clinical_knowledge",
        ("lifestyle",),
    ),
    SmokeCase(
        "case_4",
        "请结合指南说明高血压管理",
        "clinical_guidelines",
        ("clinical_guideline",),
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        default=os.getenv("MEDICAL_KB_PATH", ""),
        help="Path to an isolated Milvus Lite database copy (or MEDICAL_KB_PATH)",
    )
    parser.add_argument(
        "--output-dir",
        default="eval/rag_native_medicalqa_smoke",
        help="Directory for JSON and Markdown smoke artifacts",
    )
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--threshold", type=float, default=0.63)
    parser.add_argument(
        "--embedding-model",
        default=os.getenv("MEDICAL_KB_EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5"),
    )
    return parser.parse_args()


def _item_record(item: Any) -> dict[str, Any]:
    return {
        "id": item.evidence_id,
        "document_id": item.document_id,
        "text": item.text,
        "source": item.source,
        "section": item.section,
        "url": item.url,
        "metadata": item.metadata,
        "score": item.score,
        "rank": item.rank,
        "admitted": item.admitted,
        "admission_reason": item.admission_reason,
        "evidence_preview": item.text[:320],
    }


def _markdown(results: dict[str, Any]) -> str:
    lines = [
        "# Native MedicalQA Retrieval Smoke",
        "",
        f"Generated: {results['generated_at_utc']}",
        f"Database: `{results['database']}`",
        f"Overall: **{results['status']}**",
        "",
        "## Schema",
        "",
        "```json",
        json.dumps(results["schema"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## Cases",
        "",
    ]
    for case in results["cases"]:
        lines.extend(
            [
                f"### {case['case_id']}: {case['query']}",
                "",
                f"- Status: **{case['status']}**",
                f"- Collection: `{case['logical_collection']}` → "
                f"`{case['physical_collection']}`",
                f"- top_k: {case['top_k']}",
                f"- candidates / accepted: {case['candidate_count']} / "
                f"{case['accepted_count']}",
                f"- scores: {case['scores']}",
                f"- accepted sections: {case['accepted_sections']}",
                "",
            ]
        )
        for item in case["evidence"]:
            lines.extend(
                [
                    f"- `{item['id']}` score={item['score']:.6f} "
                    f"section={item['section']!r} source={item['source']!r}",
                    f"  - Preview: {item['evidence_preview'].replace(chr(10), ' ')}",
                ]
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


async def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.top_k < 1:
        raise ValueError("--top-k must be positive")
    database = Path(args.database).resolve() if args.database else None
    config = ExternalMedicalKnowledgeConfig(
        enabled=True,
        path=str(database) if database else "",
        embedding_model=args.embedding_model,
    )
    config.validate()

    backend = build_external_medical_knowledge_backend(config)
    try:
        schema = {
            logical: await backend.validate_collection(logical)
            for logical in ("clinical_knowledge", "clinical_guidelines")
        }
        case_results = []
        for case in CASES:
            items = await backend.search(case.query, case.logical_collection, args.top_k)
            admit_evidence(items, threshold=args.threshold, maximum=args.top_k)
            admitted = [item for item in items if item.admitted]
            accepted_sections = sorted(
                {str(item.section) for item in admitted if item.section is not None}
            )
            expected = set(case.expected_sections)
            matched = any(section in expected for section in accepted_sections)
            case_results.append(
                {
                    **asdict(case),
                    "physical_collection": backend.resolved_collections[
                        case.logical_collection
                    ],
                    "top_k": args.top_k,
                    "threshold": args.threshold,
                    "candidate_count": len(items),
                    "accepted_count": len(admitted),
                    "scores": [round(item.score, 8) for item in items],
                    "accepted_sections": accepted_sections,
                    "expected_type_matched": matched,
                    "status": "PASS" if admitted and matched else "FAIL",
                    "evidence": [_item_record(item) for item in items],
                }
            )
    finally:
        await backend.close()

    return {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "database": str(database),
        "source": "MedicalQA-DX",
        "embedding": {
            "model": config.embedding_model,
            "dimension": config.expected_dimension,
            "metric": config.expected_metric,
        },
        "schema": schema,
        "cases": case_results,
        "status": "PASS" if all(case["status"] == "PASS" for case in case_results) else "FAIL",
    }


def main() -> None:
    args = parse_args()
    results = asyncio.run(run(args))
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "smoke_results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "SMOKE_REPORT.md").write_text(_markdown(results), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": results["status"],
                "database": results["database"],
                "cases": [
                    {
                        "case_id": case["case_id"],
                        "status": case["status"],
                        "physical_collection": case["physical_collection"],
                        "candidate_count": case["candidate_count"],
                        "accepted_count": case["accepted_count"],
                        "scores": case["scores"],
                        "accepted_sections": case["accepted_sections"],
                    }
                    for case in results["cases"]
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if results["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
