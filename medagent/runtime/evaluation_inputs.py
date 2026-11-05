"""Immutable snapshots for trusted Python evaluation; never accepted by HTTP APIs."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from medagent.context.contract import (
    DELIVERABLES,
    AnswerContract,
    build_answer_contract,
)
from medagent.context.evidence_spec import EvidenceRequirement, normalize_evidence_requirements
from medagent.context.request_spec import RequestSpec
from medagent.memory.session import SessionMemory
from medagent.planning.models import Plan, Subtask


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def content_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _json_object(text: str) -> dict[str, Any]:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate frozen JSON key")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError("non-finite frozen JSON value")

    if type(text) is not str:
        raise ValueError("frozen JSON must be a string")
    value = json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_constant)
    if not isinstance(value, dict):
        raise ValueError("frozen JSON must contain an object")
    return value


def request_deliverables(spec: RequestSpec) -> dict[str, set[str]]:
    """Declared types map exactly; UNKNOWN/None has only the generic fallback."""
    return {item.id: {item.semantic_type if item.semantic_type not in {None, "UNKNOWN"}
                      else "COMPREHENSIVE_CASE_ANALYSIS"} for item in spec.items}


def memory_conditions(memory: SessionMemory, history: list[dict[str, str]]) -> dict[str, Any]:
    return {"implementation": f"{type(memory).__module__}.{type(memory).__qualname__}",
            "enabled": memory.enabled, "recent_limit": memory.recent_limit,
            "token_budget": memory.token_budget,
            "history": json.loads(canonical_json(history))}


_PROHIBITION = re.compile(
    r"^(?:please\s+|请\s*)?(?:do not\b|don't\b|must not\b|never\b|不要|不得|禁止)\s*", re.I,
)


def _prohibited_scope(restrictions: tuple[str, ...]) -> set[str]:
    # Only complete, canonical direct clauses have a deterministic scope. A mixed
    # clause, quote or exception must not be classified by keyword occurrence.
    names = {d.replace("_", " ").casefold(): {d} for d in DELIVERABLES}
    diagnosis = {"DIAGNOSIS_WITH_BASIS", "MOST_LIKELY_DIAGNOSIS",
                 "DIAGNOSTIC_BASIS", "DIAGNOSIS_LIST"}
    treatment = {"TREATMENT_PLAN", "TREATMENT_PRINCIPLES", "BEST_TREATMENT_PLAN"}
    names.update({"diagnosis": diagnosis, "diagnose": diagnosis, "诊断": diagnosis,
                  "treatment": treatment, "治疗": treatment, "治疗方案": treatment,
                  "手术指征": {"SURGERY_INDICATION"}})
    prohibited: set[str] = set()
    for text in restrictions:
        match = _PROHIBITION.match(text)
        if match:
            payload = text[match.end():].strip().rstrip(".。!！")
            payload = re.sub(r"^(?:provide|give|include|discuss|perform|make|recommend)\s+"
                             r"|^(?:提供|给出|制定|讨论|进行)", "", payload, flags=re.I)
            payload = re.sub(r"^(?:a|an|the|any)\s+", "", payload, flags=re.I)
            prohibited.update(names.get(" ".join(payload.casefold().split()), set()))
    return prohibited


@dataclass(frozen=True, slots=True)
class FrozenRequest:
    description: str
    question: str
    spec_json: str
    contract_json: str
    restrictions: tuple[str, ...]
    external_evidence_json: str | None = None

    def __post_init__(self) -> None:
        if (type(self.description) is not str or type(self.question) is not str
                or not isinstance(self.restrictions, (list, tuple))
                or any(type(r) is not str or not r.strip() for r in self.restrictions)):
            raise ValueError("invalid frozen request scalar fields")
        object.__setattr__(self, "restrictions", tuple(self.restrictions))
        object.__setattr__(self, "spec_json", canonical_json(_json_object(self.spec_json)))
        object.__setattr__(self, "contract_json", canonical_json(_json_object(self.contract_json)))
        if self.external_evidence_json is not None:
            value = _json_object(self.external_evidence_json)
            if set(value) != {"requirements"}:
                raise ValueError("invalid frozen evidence envelope")
            declarations = normalize_evidence_requirements(
                RequestSpec.from_dict(_json_object(self.spec_json)), value["requirements"],
            )
            object.__setattr__(self, "external_evidence_json", canonical_json({
                "requirements": [r.to_dict() for r in declarations],
            }))

    @classmethod
    def capture(
        cls, description: str, question: str, spec: RequestSpec,
        contract: AnswerContract, restrictions: list[str] | tuple[str, ...] = (),
        *, external_evidence_requirements: list[dict[str, Any]] | None = None,
    ) -> FrozenRequest:
        if not isinstance(restrictions, (list, tuple)):
            raise ValueError("restrictions must be a sequence of source spans")
        snapshot = cls(description, question, canonical_json(spec.to_dict()),
                       canonical_json(contract.to_dict()), tuple(restrictions),
                       canonical_json({"requirements": external_evidence_requirements})
                       if external_evidence_requirements is not None else None)
        snapshot.materialize(description, question)
        return snapshot

    @property
    def sha256(self) -> str:
        value = {"description": self.description, "question": self.question,
                 "spec": json.loads(self.spec_json), "contract": json.loads(self.contract_json),
                 "restrictions": self.restrictions}
        if self.external_evidence_json is not None:
            value["external_evidence_requirements"] = json.loads(self.external_evidence_json)
        return content_sha256(value)

    @property
    def evidence_requirements(self) -> tuple[EvidenceRequirement, ...] | None:
        if self.external_evidence_json is None:
            return None
        return normalize_evidence_requirements(
            RequestSpec.from_dict(json.loads(self.spec_json)),
            json.loads(self.external_evidence_json)["requirements"],
        )

    def materialize(
        self, description: str, question: str,
    ) -> tuple[RequestSpec, AnswerContract, list[str]]:
        if (description != self.description or question != self.question
                or not description.strip() or not question.strip()):
            raise ValueError("frozen request does not match current input")
        raw_spec = json.loads(self.spec_json)
        if (not isinstance(raw_spec, dict) or set(raw_spec) != {"items"}
                or not isinstance(raw_spec["items"], list)):
            raise ValueError("invalid frozen request schema")
        for index, item in enumerate(raw_spec["items"], 1):
            if (not isinstance(item, dict) or set(item) != {
                    "id", "text", "required", "order", "source_span", "semantic_type"}
                    or item["id"] != f"RQ{index}" or type(item["required"]) is not bool
                    or type(item["order"]) is not int or item["order"] != index
                    or not isinstance(item["source_span"], str)
                    or not item["source_span"].strip() or item["source_span"] not in question
                    or not isinstance(item["text"], str) or not item["text"].strip()
                    or not (item["semantic_type"] is None
                            or isinstance(item["semantic_type"], str)
                            and item["semantic_type"] in DELIVERABLES | {"UNKNOWN"})):
                raise ValueError("invalid frozen request mapping")
        spec = RequestSpec.from_dict(raw_spec)
        contract = AnswerContract.from_dict(json.loads(self.contract_json))
        labels = [*contract.must_cover, *contract.optional_cover,
                  *(c.item for c in contract.coverage_checklist)]
        if any("_".join(label.replace("_", " ").upper().split())
               not in contract.requested_deliverables for label in labels):
            raise ValueError("unknown frozen contract coverage label")
        if any(not isinstance(r, str) or not r.strip() or r not in question
               for r in self.restrictions):
            raise ValueError("restrictions must map to the current question")
        mapping = request_deliverables(spec)
        scope = set().union(*mapping.values())
        must = set().union(*(mapping[item.id] for item in spec.items if item.required))
        safety = set(build_answer_contract("").avoid) | set(self.restrictions)
        if (scope != set(contract.requested_deliverables)
                or must != set(contract.required_deliverable_ids)
                or not safety.issubset(contract.avoid)
                or scope & _prohibited_scope(self.restrictions)):
            raise ValueError("frozen contract contradicts request scope or restrictions")
        # MUST/OPTIONAL conflicts must not be hidden by priority union semantics.
        required = set(contract.required_deliverable_ids)
        optional = {"_".join(s.replace("_", " ").upper().split()) for s in contract.optional_cover}
        optional.update("_".join(c.item.replace("_", " ").upper().split())
                        for c in contract.coverage_checklist
                        if c.priority == "OPTIONAL")
        if required & optional or optional != scope - must:
            raise ValueError("contradictory frozen contract priorities")
        if self.external_evidence_json is not None:
            normalize_evidence_requirements(
                spec, json.loads(self.external_evidence_json)["requirements"],
            )
        return spec, contract, list(self.restrictions)


@dataclass(frozen=True, slots=True)
class FrozenExecutionPlan:
    plan_json: str
    sha256: str
    request_sha256: str
    conditions_sha256: str

    def __post_init__(self) -> None:
        for digest in (self.sha256, self.request_sha256, self.conditions_sha256):
            if type(digest) is not str or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError("invalid frozen plan digest")
        object.__setattr__(self, "plan_json", canonical_json(_json_object(self.plan_json)))

    @classmethod
    def capture(
        cls, plan: Plan, request: FrozenRequest, conditions: dict[str, Any],
    ) -> FrozenExecutionPlan:
        value = plan.to_dict()
        return cls(canonical_json(value), content_sha256(value), request.sha256,
                   content_sha256(conditions))

    def to_dict(self) -> dict[str, str | int]:
        return {"version": 1, "plan_json": self.plan_json, "sha256": self.sha256,
                "request_sha256": self.request_sha256,
                "conditions_sha256": self.conditions_sha256}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> FrozenExecutionPlan:
        if (set(value) != {"version", "plan_json", "sha256", "request_sha256",
                           "conditions_sha256"}
                or type(value["version"]) is not int or value["version"] != 1):
            raise ValueError("invalid frozen plan envelope")
        for key in ("sha256", "request_sha256", "conditions_sha256"):
            if not isinstance(value[key], str) or not re.fullmatch(r"[0-9a-f]{64}", value[key]):
                raise ValueError("invalid frozen plan digest")
        if not isinstance(value["plan_json"], str):
            raise ValueError("invalid frozen plan JSON")
        snapshot = cls(**{k: v for k, v in value.items() if k != "version"})
        snapshot.materialize()
        return snapshot

    def materialize(self) -> Plan:
        value = json.loads(self.plan_json)
        if content_sha256(value) != self.sha256 or canonical_json(value) != self.plan_json:
            raise ValueError("frozen plan digest or canonical form mismatch")
        data = dict(value)
        parse_failure = data.pop("planner_parse_failure")
        data["subtasks"] = [Subtask(**item) for item in data["subtasks"]]
        plan = Plan(**data)
        plan.validate()
        if type(parse_failure) is not bool or parse_failure != plan.planner_parse_failure:
            raise ValueError("inconsistent frozen plan metadata")
        return plan

    def validate_for(
        self, request: FrozenRequest, conditions: dict[str, Any], max_subtasks: int,
    ) -> Plan:
        if (self.request_sha256 != request.sha256
                or self.conditions_sha256 != content_sha256(conditions)):
            raise ValueError("frozen plan request or execution conditions mismatch")
        plan = self.materialize()
        plan.validate_budget(max_subtasks)
        spec, contract, _ = request.materialize(request.description, request.question)
        mapping = request_deliverables(spec)
        valid_ids = {item.id for item in spec.items}
        covered: set[str] = set()
        delivered: set[str] = set()
        for task in plan.subtasks:
            if (not isinstance(task.request_item_ids, list) or not task.request_item_ids
                    or any(not isinstance(r, str) or r not in valid_ids
                           for r in task.request_item_ids)
                    or len(set(task.request_item_ids)) != len(task.request_item_ids)
                    or not isinstance(task.deliverable_ids, list) or not task.deliverable_ids
                    or any(not isinstance(d, str) or d not in contract.requested_deliverables
                           for d in task.deliverable_ids)
                    or len(set(task.deliverable_ids)) != len(task.deliverable_ids)):
                raise ValueError("invalid frozen plan RQ or contract mapping")
            covered.update(task.request_item_ids)
            delivered.update(task.deliverable_ids)
            if set(task.deliverable_ids) != set().union(
                *(mapping[request_id] for request_id in task.request_item_ids)
            ):
                raise ValueError("frozen plan cross-RQ deliverable mapping")
        if (not set(spec.required_item_ids).issubset(covered)
                or not set(contract.required_deliverable_ids).issubset(delivered)):
            raise ValueError("frozen plan omits required scope")
        for requirement in request.evidence_requirements or ():
            if requirement.execution != "reuse_only":
                continue
            for task in plan.subtasks:
                if requirement.request_item_id not in task.request_item_ids:
                    continue
                ancestors = set(task.depends_on)
                while True:
                    expanded = ancestors | {dependency for parent in plan.subtasks
                                            if parent.subtask_id in ancestors
                                            for dependency in parent.depends_on}
                    if expanded == ancestors:
                        break
                    ancestors = expanded
                for upstream in requirement.upstream_request_item_ids:
                    if not any(parent.subtask_id in ancestors
                               and upstream in parent.request_item_ids
                               for parent in plan.subtasks):
                        raise ValueError("frozen evidence reuse requires upstream task dependency")
        return plan


@dataclass(slots=True)
class EvaluationRun:
    result: dict[str, Any]
    plan: FrozenExecutionPlan | None
