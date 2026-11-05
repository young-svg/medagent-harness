"""JSON Schema definition for candidate documents; no execution adapter."""

from __future__ import annotations


def candidate_schema():
    string = {"type": "string", "minLength": 1}
    boolean = {"type": "boolean"}
    integer = {"type": "integer", "minimum": 0}

    def obj(properties, *, optional=()):
        return {
            "type": "object",
            "properties": properties,
            "required": [p for p in properties if p not in optional],
            "additionalProperties": False,
        }

    def array(items, minimum=0):
        return {"type": "array", "items": items, "minItems": minimum}

    def ref(name):
        return {"$ref": "#/$defs/" + name}

    strings = array(string)
    rq = {"type": "string", "pattern": "^RQ[1-9][0-9]*$"}
    case_id = {"type": "string", "pattern": "^[SIDEM][0-9]{2}$"}
    status = {"const": "CANDIDATE_PENDING_HUMAN_APPROVAL"}
    version = {"const": "w7-2b-v1-candidate"}
    kinds = {"enum": ["guideline", "research", "classification", "external"]}
    span = obj({"text": string, "start": integer, "end": integer})
    item = obj(
        {
            "id": rq,
            "text": string,
            "required": boolean,
            "order": {"type": "integer", "minimum": 1},
            "source_span": string,
            "semantic_type": string,
        }
    )
    contract = obj(
        {
            "intent": string,
            "requested_deliverables": array(string, 1),
            "response_mode": string,
            "must_cover": strings,
            "optional_cover": strings,
            "avoid": strings,
            "breadth": string,
            "coverage_checklist": array(
                obj(
                    {
                        "item": string,
                        "priority": {"enum": ["MUST", "OPTIONAL"]},
                        "covered": {"const": False},
                    }
                ),
                1,
            ),
        }
    )
    evidence = obj(
        {
            "request_item_id": rq,
            "source_category": kinds,
            "acceptable_source_categories": array(kinds, 1),
            "topic": string,
            "required": boolean,
            "execution": {"enum": ["retrieve", "reuse_only"]},
            "upstream_request_item_ids": array(rq),
            "requires_admission_delivery_citation": {"const": True},
        }
    )
    source = obj(
        {
            "evidence_id": string,
            "document_id": string,
            "source_id": string,
            "title": string,
            "section": string,
            "source": string,
            "url": {"type": "null"},
            "text": string,
            "score": {"type": "number", "minimum": 0, "maximum": 1},
            "rank": {"type": "integer", "minimum": 1},
            "admitted": {"const": False},
            "admission_reason": {"const": "not_evaluated"},
            "collection": {"enum": ["clinical_knowledge", "clinical_guidelines"]},
            "topic": string,
            "metadata": obj(
                {
                    "source_kind": kinds,
                    "synthetic": {"const": True},
                    "fixture_version": version,
                    "fixture_date": string,
                    "real_world_currentness": {"const": "NOT_ASSERTED"},
                }
            ),
        }
    )
    grade_values = obj(
        {
            "satisfied": {"const": 1},
            "partial": {"const": 0.5},
            "unanswered": {"const": 0},
            "off_topic": {"const": 0},
            "insufficient_information": {"type": "null"},
            "uncertain": {"type": "null"},
            "execution_error": {"const": 0},
        }
    )
    rubric = obj(
        {
            "PASS": array(string, 1),
            "PARTIAL": array(string, 1),
            "FAIL": array(string, 1),
            "per_item": grade_values,
            "aggregation": string,
            "optional_scored_separately": {"const": True},
            "w2_is_not_independent_ground_truth": {"const": True},
            "human_scoring": string,
            "item_specific_criteria": array(
                obj(
                    {
                        "request_item_id": rq,
                        "criterion": string,
                        "source_completion_condition": string,
                    }
                ),
                1,
            ),
            "memory_off": obj({"task_grade": string, "safe_behavior": string}),
        },
        optional=("memory_off",),
    )
    case = obj(
        {
            "case_id": case_id,
            "category": {
                "enum": ["simple", "independent", "dependent", "external_evidence", "memory"]
            },
            "fixture_version": version,
            "approval_status": status,
            "original_design": obj(
                {
                    "report_section": {"const": "4"},
                    "table_columns": {
                        "type": "array",
                        "items": string,
                        "minItems": 5,
                        "maxItems": 5,
                    },
                    "original_input": string,
                    "original_description": {"type": "string"},
                    "original_question": string,
                    "original_expected_request_spec": string,
                    "original_expected_contract": string,
                    "original_completion_condition": string,
                }
            ),
            "description": string,
            "question": string,
            "offset_convention": string,
            "expected_request_spec": obj({"items": array(item, 1)}),
            "source_mappings": array(
                obj(
                    {
                        "request_item_id": rq,
                        "field": {"const": "question"},
                        "text": string,
                        "start": integer,
                        "end": integer,
                        "original_question_span": span,
                        "source_context_spans": array(span),
                    }
                ),
                1,
            ),
            "expected_answer_contract": contract,
            "restrictions": strings,
            "restriction_mappings": array(span),
            "must_request_item_ids": array(rq, 1),
            "optional_request_item_ids": array(rq),
            "expected_dependencies": array(obj({"from": rq, "to": rq})),
            "dependency_policy": string,
            "external_evidence_requirements": array(evidence),
            "session_layout": obj(
                {
                    "active_session": {"const": "A"},
                    "sessions": {
                        "type": "object",
                        "required": ["A"],
                        "additionalProperties": array(
                            obj({"role": {"const": "user"}, "content": string})
                        ),
                    },
                    "isolate_per_case_and_group": {"const": True},
                    "seed_mode": string,
                }
            ),
            "history_dependency": {
                "enum": ["none", "required", "current_sufficient_overrides_history"]
            },
            "completion_rubric": rubric,
            "forbidden_claims": array(string, 1),
            "human_review_required": array(string, 1),
            "required_tool_policy": string,
        }
    )
    header = {"fixture_version": version, "approval_status": status}
    cases_doc = obj(
        {
            "schema_version": {"const": 1},
            **header,
            "cases": {"type": "array", "items": ref("case"), "minItems": 24, "maxItems": 24},
        }
    )
    binding = obj(
        {
            "case_id": case_id,
            "request_item_id": rq,
            "execution": {"enum": ["retrieve", "reuse_only"]},
            "topic": string,
            "source_category": kinds,
            "acceptable_source_categories": array(kinds, 1),
            "success_source_ids": array(string, 1),
            "success_source_ids_semantics": {"const": "catalogue_examples_not_whitelist"},
            "tools": array(string, 1),
        }
    )
    retrieval_case = obj(
        {
            "case_id": case_id,
            "request_item_id": rq,
            "variant": {"enum": ["success", "no_results", "low_score", "wrong_category"]},
            "records": array(ref("source")),
            "applicable": boolean,
            "selection_required_before_return": array(string, 1),
            "expected_evidence_gate": {
                "enum": ["eligible_not_sufficient", "not_satisfied", "not_applicable"]
            },
        }
    )
    corpus_doc = obj(
        {
            **header,
            "synthetic_only": {"const": True},
            "admission_threshold": {"const": 0.63},
            "top_k": {"const": 5},
            "source_records": array(ref("source"), 1),
            "request_bindings": array(binding, 1),
            "retrieval_cases": array(retrieval_case, 1),
            "query_rules": obj({
                "version": {"const": "w7-query-selection-v1.1-candidate"},
                "normalization": {"const": "NFKC_casefold_collapse_whitespace"},
                "matching": {"const": "all_concept_groups_any_alias"},
                "latin_match": {"const": "whole_words_or_phrases"},
                "han_match": {"const": "substring"},
                "multiple_topics": {"const": "reject_ambiguous"},
                "rq_only_fallback": {"const": False},
                "topic_concept_groups": obj({
                    "cough": array(array(string, 1), 1),
                    "exercise_sleep": array(array(string, 1), 2),
                    "hydration_headache": array(array(string, 1), 2),
                }),
                "tool_collections": obj({
                    "search_knowledge": {"const": "clinical_knowledge"},
                    "clinical_guideline": {"const": "clinical_guidelines"},
                    "disease_code": {"const": "clinical_guidelines"},
                }),
            }),
            "query_match_examples": array(obj({
                "example_id": string, "case_id": case_id, "request_item_id": rq,
                "query": {"type": "string"}, "tool": string, "collection": string,
                "candidate_source_ids": array(string, 1),
                "expected_status": {"enum": ["eligible", "no_topic_match", "wrong_topic",
                                               "ambiguous_topic", "tool_denied",
                                               "collection_mismatch", "source_ineligible"]},
                "expected_source_ids": strings,
            }), 1),
            "selector_contract": obj(
                {
                    "implemented": {"const": False},
                    "must_match": strings,
                    "no_match": string,
                    "ambiguous_match": string,
                    "source_id_policy": {
                        "const": "catalogue_membership_required_not_example_id_whitelist",
                    },
                    "reference_predicate": {
                        "const": "validate_candidates.py::select_fixture_candidates",
                    },
                    "warning": string,
                }
            ),
            "response_variants": {
                "type": "object",
                "additionalProperties": {
                    "type": "object",
                    "required": ["selection"],
                    "properties": {"selection": string, "score": {"type": "number"}},
                    "additionalProperties": False,
                },
            },
            "limitations": array(string, 1),
        }
    )
    faults_doc = obj(
        {
            **header,
            "implementation_status": {"const": "DECLARATIVE_NOT_EXECUTED"},
            "variants": array(
                obj(
                    {
                        "variant_id": string,
                        "base_case_id": case_id,
                        "injection": string,
                        "expected_behavior": string,
                        "scope": string,
                        "human_approval_required": {"const": True},
                    }
                ),
                1,
            ),
            "payloads": obj(
                {
                    "injection": string,
                    "cross_collection_conflict": obj(
                        {"evidence_id": string, "collections": strings, "texts": strings}
                    ),
                }
            ),
            "recovery_comparison": obj(
                {"infrastructure_off": string, "length_off": string, "protocol_off": string}
            ),
            "http400": string,
        }
    )
    changes_doc = obj(
        {
            "approval_status": status,
            "changes": array(
                obj(
                    {
                        "case_id": case_id,
                        "field": string,
                        "applied": boolean,
                        "before": {"type": "string"},
                        "after": string,
                        "reason": string,
                    }
                )
            ),
            "original_questions_rewritten": array(case_id),
            "note": string,
        }
    )
    provenance_doc = obj(
        {
            **header,
            "core_interfaces_commit": string,
            "original_w6_baseline": string,
            "source_documents": array(
                obj({"path": string, "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"}}), 2
            ),
            "authority": string,
            "annotation_method": string,
            "frozen": {"const": False},
        }
    )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "W7-2B candidate documents (not approved or frozen)",
        "$defs": {
            "case": case,
            "source": source,
            "cases_document": cases_doc,
            "corpus_document": corpus_doc,
            "faults_document": faults_doc,
            "changes_document": changes_doc,
            "provenance_document": provenance_doc,
        },
        "anyOf": [
            {"$ref": "#/$defs/" + name}
            for name in (
                "cases_document",
                "corpus_document",
                "faults_document",
                "changes_document",
                "provenance_document",
            )
        ],
    }
