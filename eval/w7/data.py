"""Read-only frozen dataset adapter and frozen query selector."""

from __future__ import annotations

import importlib.util
import json
from copy import deepcopy
from dataclasses import fields
from hashlib import sha256
from pathlib import Path

from medagent.context.contract import AnswerContract
from medagent.context.request_spec import RequestSpec
from medagent.memory.session import SessionMemory
from medagent.retrieval.backend import FakeRetrievalBackend
from medagent.retrieval.evidence import EvidenceItem
from medagent.runtime.evaluation_inputs import FrozenRequest

from .budget import tool_scope

ROOT = Path(__file__).resolve().parents[2] / "experiments/w7/frozen_v1"
SMOKE_CASES = ("S02", "I03", "D03", "E03", "M01")


def load_case(case_id):
    if case_id not in SMOKE_CASES:
        raise ValueError("first_phase_supports_only_five_smoke_cases")
    manifest = json.loads((ROOT / "freeze/manifest.json").read_text(encoding="utf-8"))
    for name, digest in manifest["approved_candidate_files"].items():
        if sha256((ROOT / name).read_bytes()).hexdigest() != digest:
            raise ValueError("frozen_dataset_integrity_failure")
    cases = json.loads((ROOT / "cases.json").read_text(encoding="utf-8"))["cases"]
    return next(c for c in cases if c["case_id"] == case_id)


def frozen_request(case):
    return FrozenRequest.capture(
        case["description"],
        case["question"],
        RequestSpec.from_dict(case["expected_request_spec"]),
        AnswerContract.from_dict(case["expected_answer_contract"]),
        case["restrictions"],
        external_evidence_requirements=case["external_evidence_requirements"],
    )


def seeded_memory(case, enabled):
    memory = SessionMemory()
    # Preserve the same stored seed even when reads/writes are disabled.
    for session, messages in case["session_layout"]["sessions"].items():
        for message in messages:
            memory.add(session, message["role"], message["content"])
    memory.enabled = enabled
    return memory


def dataset_sha():
    return sha256((ROOT / "freeze/manifest.json").read_bytes()).hexdigest()


class FrozenCorpusBackend(FakeRetrievalBackend):
    def __init__(self, case_id, *, fault=None, enabled=True):
        self.document = json.loads((ROOT / "retrieval_corpus.json").read_text(encoding="utf-8"))
        self.case_id, self.fault, self.enabled = case_id, fault, enabled
        super().__init__(
            [
                EvidenceItem(**{f.name: deepcopy(row[f.name]) for f in fields(EvidenceItem)})
                for row in self.document["source_records"]
            ]
        )
        spec = importlib.util.spec_from_file_location(
            "w7_frozen_selector", ROOT / "validate_candidates.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.select = module.select_fixture_candidates

    async def search(self, query, collection, top_k):
        from medagent.retrieval.backend import RetrievalDisabledError

        if not self.enabled:
            raise RetrievalDisabledError("offline_rag_disabled")
        scope = tool_scope.get()
        if scope is None or not scope[0]:
            raise ValueError("missing_trusted_request_scope")
        example = {
            "case_id": self.case_id,
            "request_item_id": scope[0],
            "tool": scope[1],
            "query": query,
            "collection": collection,
            "candidate_source_ids": [item.evidence_id for item in self.items],
        }
        status, identifiers = self.select(self.document, example)
        self.requests.append(
            {
                "request_item_id": scope[0],
                "tool": scope[1],
                "collection": collection,
                "selector_status": status,
            }
        )
        if self.fault == "empty":
            identifiers = []
        if self.fault == "classification_only" and scope[0] == "RQ2":
            identifiers = ["SYNTHETIC-C-COUGH"]
        return [deepcopy(item) for item in self.items if item.evidence_id in identifiers][:top_k]
