"""Validate candidate data/annotations only: no Runner, LLM, retrieval, or Trace."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))

from medagent.context.contract import AnswerContract  # noqa: E402
from medagent.context.request_spec import RequestSpec  # noqa: E402
from medagent.runtime.evaluation_inputs import FrozenRequest  # noqa: E402

DOCS = {
    "cases.json": "cases_document",
    "retrieval_corpus.json": "corpus_document",
    "fault_variants.json": "faults_document",
    "wording_changes.json": "changes_document",
    "provenance.json": "provenance_document",
}


def check(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    def pairs(items):
        result = {}
        for k, v in items:
            check(k not in result, f"duplicate JSON key: {k}")
            result[k] = v
        return result

    def invalid(value):
        raise ValueError(f"nonfinite JSON: {value}")

    return json.loads(
        path.read_text(encoding="utf-8"), object_pairs_hook=pairs, parse_constant=invalid
    )


def validate_shape(value, schema, root, path="$ "):
    """Strictly implement only the documented keywords emitted by our Schema.

    This is not advertised as a general-purpose JSON Schema implementation.
    Unknown validation keywords are rejected rather than silently ignored.
    """
    allowed = {
        "$ref",
        "type",
        "const",
        "enum",
        "minLength",
        "pattern",
        "minimum",
        "maximum",
        "minItems",
        "maxItems",
        "items",
        "properties",
        "required",
        "additionalProperties",
    }
    check(not set(schema) - allowed, f"unsupported schema keywords at {path}")
    if "$ref" in schema:
        return validate_shape(value, root["$defs"][schema["$ref"].split("/")[-1]], root, path)
    if "const" in schema:
        check(type(value) is type(schema["const"]) and value == schema["const"], f"const: {path}")
    if "enum" in schema:
        check(value in schema["enum"], f"enum: {path}")
    if "type" in schema:
        kind = schema["type"]
        valid = {
            "string": isinstance(value, str),
            "boolean": type(value) is bool,
            "integer": type(value) is int,
            "number": type(value) in {int, float},
            "null": value is None,
            "object": type(value) is dict,
            "array": type(value) is list,
        }
        check(valid[kind], f"type {kind}: {path}")
    if isinstance(value, str):
        check(len(value) >= schema.get("minLength", 0), f"string length: {path}")
        if "pattern" in schema:
            check(re.fullmatch(schema["pattern"], value) is not None, f"pattern: {path}")
    if type(value) in {int, float}:
        check(math.isfinite(value), f"finite: {path}")
        check(value >= schema.get("minimum", -math.inf), f"minimum: {path}")
        check(value <= schema.get("maximum", math.inf), f"maximum: {path}")
    if isinstance(value, list):
        check(len(value) >= schema.get("minItems", 0), f"minItems: {path}")
        check(len(value) <= schema.get("maxItems", math.inf), f"maxItems: {path}")
        for i, item in enumerate(value):
            validate_shape(item, schema.get("items", {}), root, f"{path}[{i}]")
    if isinstance(value, dict):
        check(set(schema.get("required", [])) <= value.keys(), f"required fields: {path}")
        properties = schema.get("properties", {})
        for key, item in value.items():
            rule = properties.get(key, schema.get("additionalProperties", True))
            check(rule is not False, f"unexpected field: {path}.{key}")
            if isinstance(rule, dict):
                validate_shape(item, rule, root, f"{path}.{key}")


def original_rows():
    from prepare_candidates import ANNOTATIONS, rows

    report = rows((ROOT / "W7-1_original_design_report.md").read_text(encoding="utf-8"))
    draft = rows((ROOT / "W7-1_24_case_draft_original.md").read_text(encoding="utf-8"))
    check(report == draft and set(report) == set(ANNOTATIONS), "original case tables disagree")
    return report


def validate_cases(document, originals):
    from prepare_candidates import EDGES, HISTORY, RESTRICTIONS

    cases = document["cases"]
    expected = [
        f"{prefix}{i:02}"
        for prefix, n in [("S", 4), ("I", 5), ("D", 5), ("E", 6), ("M", 4)]
        for i in range(1, n + 1)
    ]
    check([c["case_id"] for c in cases] == expected, "case IDs/count/order changed")
    counts = Counter(c["category"] for c in cases)
    check(
        counts
        == {"simple": 4, "independent": 5, "dependent": 5, "external_evidence": 6, "memory": 4},
        "category counts",
    )
    for case in cases:
        name, question = case["case_id"], case["question"]
        check(case["original_design"]["table_columns"] == originals[name], name + " original table")
        check(
            case["original_design"]["original_input"] == originals[name][1],
            name + " original input",
        )
        original_question = case["original_design"]["original_question"]
        check(original_question in originals[name][1], name + " original question not in original")
        spec = RequestSpec.from_dict(case["expected_request_spec"])
        contract = AnswerContract.from_dict(case["expected_answer_contract"])
        # Exercise the frozen interface's deterministic schema/semantic checks only.
        # No analyze(), model, tool, Memory writes, or Trace recorder is invoked.
        FrozenRequest.capture(case["description"], question, spec, contract, case["restrictions"])
        check(
            case["must_request_item_ids"] == [i.id for i in spec.items if i.required],
            name + " MUST",
        )
        check(
            case["optional_request_item_ids"] == [i.id for i in spec.items if not i.required],
            name + " OPTIONAL",
        )
        check(len(case["source_mappings"]) == len(spec.items), name + " mapping count")
        for item, mapping in zip(spec.items, case["source_mappings"], strict=True):
            check(item.id == mapping["request_item_id"], name + " RQ mapping")
            check(mapping["text"] == item.source_span, name + " source text")
            for s in [mapping, *mapping["source_context_spans"]]:
                check(0 <= s["start"] < s["end"] <= len(question), name + " offset bounds")
                check(question[s["start"] : s["end"]] == s["text"], name + " source slice")
            s = mapping["original_question_span"]
            check(
                original_question[s["start"] : s["end"]] == s["text"] == item.source_span,
                name + " original source slice",
            )
        check(case["restrictions"] == RESTRICTIONS.get(name, []), name + " restrictions")
        check(
            len(case["restriction_mappings"]) == len(case["restrictions"]),
            name + " restriction count",
        )
        for text, s in zip(case["restrictions"], case["restriction_mappings"], strict=True):
            check(question[s["start"] : s["end"]] == s["text"] == text, name + " restriction span")
        edges = {(e["from"], e["to"]) for e in case["expected_dependencies"]}
        check(
            edges == {(f"RQ{a}", f"RQ{b}") for a, b in EDGES.get(name, [])},
            name + " original edges",
        )
        ids = {i.id for i in spec.items}
        seen = set()
        while len(seen) < len(ids):
            ready = {rq for rq in ids - seen if all(a in seen for a, b in edges if b == rq)}
            check(bool(ready), name + " cyclic DAG")
            seen |= ready
        expected_history = HISTORY.get(name, {"A": []})
        check(
            case["session_layout"]["sessions"]
            == {
                s: [{"role": "user", "content": t} for t in messages]
                for s, messages in expected_history.items()
            },
            name + " history altered",
        )
        for requirement in case["external_evidence_requirements"]:
            rq = next((i for i in spec.items if i.id == requirement["request_item_id"]), None)
            check(
                rq is not None and rq.required == requirement["required"],
                name + " evidence priority",
            )
            if requirement["execution"] == "reuse_only":
                check(
                    all((up, rq.id) in edges for up in requirement["upstream_request_item_ids"]),
                    name + " handoff relation",
                )
    return dict(counts)


def validate_corpus(document, cases):
    by_case = {c["case_id"]: c for c in cases}
    sources = {s["evidence_id"]: s for s in document["source_records"]}
    check(len(sources) == len(document["source_records"]), "duplicate corpus Evidence IDs")
    for source in sources.values():
        check(
            all(
                "SYNTHETIC" in source[field]
                for field in ("evidence_id", "document_id", "source_id", "title", "source", "text")
            ),
            "missing SYNTHETIC label",
        )
        check(source["score"] == 0.90, "base source score drift")
    bindings = {(b["case_id"], b["request_item_id"]): b for b in document["request_bindings"]}
    expected = {
        (c["case_id"], r["request_item_id"]): r
        for c in cases
        for r in c["external_evidence_requirements"]
    }
    check(
        len(bindings) == len(document["request_bindings"]) and set(bindings) == set(expected),
        "evidence bindings missing/duplicated",
    )
    for key, binding in bindings.items():
        requirement = expected[key]
        check(
            binding["source_category"] == requirement["source_category"], "source category mapping"
        )
        check(binding["topic"] == requirement["topic"], "source topic mapping")
        check(binding["acceptable_source_categories"] ==
              requirement["acceptable_source_categories"],
              "acceptable source categories changed")
        kind = requirement["source_category"]
        approved_tools = (["disease_code"] if kind == "classification" else
                          ["search_knowledge", "clinical_guideline"] if kind == "guideline"
                          else ["search_knowledge"])
        check(binding["tools"] == approved_tools, "binding tool permissions expanded")
        for identifier in binding["success_source_ids"]:
            check(identifier in sources, "unknown source ID")
            s = sources[identifier]
            check(
                s["topic"] == binding["topic"]
                and s["metadata"]["source_kind"] in requirement["acceptable_source_categories"],
                "unrelated success source",
            )
            check(s["collection"] in {document["query_rules"]["tool_collections"][t]
                                       for t in binding["tools"]}, "unreachable example source")
        check(key[0] in by_case, "unknown binding case")
    seen = set()
    for response in document["retrieval_cases"]:
        key = response["case_id"], response["request_item_id"]
        variant = response["variant"]
        check((*key, variant) not in seen, "duplicate retrieval variant")
        seen.add((*key, variant))
        check(
            key in expected and expected[key]["execution"] == "retrieve",
            "unexpected retrieve/reuse",
        )
        requirement = expected[key]
        for s in response["records"]:
            check(s["evidence_id"] in sources, "variant source not in catalogue")
            original = sources[s["evidence_id"]]
            comparison = dict(s)
            if variant == "low_score":
                comparison["score"] = 0.90
            check(comparison == original, "fixture content changed beyond declared score override")
            check(s["topic"] == requirement["topic"], "variant topic mismatch")
            check(s["score"] == (0.20 if variant == "low_score" else 0.90), "variant score")
            allowed = s["metadata"]["source_kind"] in requirement["acceptable_source_categories"]
            check(allowed != (variant == "wrong_category"), "category variant not distinct")
        if variant == "no_results":
            check(not response["records"], "non-empty no_results")
        elif response["applicable"]:
            check(bool(response["records"]), "empty applicable variant")
        else:
            check(
                variant == "wrong_category" and requirement["source_category"] == "external",
                "invalid not-applicable variant",
            )
    wanted = {
        (*key, variant)
        for key, r in expected.items()
        if r["execution"] == "retrieve"
        for variant in ("success", "no_results", "low_score", "wrong_category")
    }
    check(seen == wanted, "retrieval variant coverage")
    validate_query_examples(document)


def select_fixture_candidates(document, example):
    """Pure review predicate over catalogue data, not a backend or Runner.

    RQ identifies eligible scope; actual query independently has to identify one
    complete topic. Example source IDs confer no authority. No tools are called.
    """
    binding = next(b for b in document["request_bindings"]
                   if (b["case_id"], b["request_item_id"]) ==
                   (example["case_id"], example["request_item_id"]))
    check(binding["execution"] == "retrieve", "reuse-only RQ cannot initiate retrieval")
    rules = document["query_rules"]
    if example["tool"] not in binding["tools"]:
        return "tool_denied", []
    if rules["tool_collections"].get(example["tool"]) != example["collection"]:
        return "collection_mismatch", []

    def normalize(text):
        return " ".join(unicodedata.normalize("NFKC", text).casefold().split())

    query = normalize(example["query"])

    def matches(alias):
        alias = normalize(alias)
        if alias.isascii():
            return re.search(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", query) is not None
        return alias in query

    topics = [topic for topic, groups in rules["topic_concept_groups"].items()
              if all(any(matches(alias) for alias in group) for group in groups)]
    if not topics:
        return "no_topic_match", []
    if len(topics) != 1:
        return "ambiguous_topic", []
    if topics[0] != binding["topic"]:
        return "wrong_topic", []
    catalogue = {s["evidence_id"]: s for s in document["source_records"]}
    check(all(i in catalogue for i in example["candidate_source_ids"]), "unknown catalogue ID")
    eligible = [identifier for identifier in example["candidate_source_ids"]
                if (s := catalogue[identifier])["collection"] == example["collection"]
                and s["topic"] == topics[0]
                and s["metadata"]["source_kind"] in binding["acceptable_source_categories"]]
    return ("eligible" if eligible else "source_ineligible"), eligible


def validate_query_examples(document):
    examples = document["query_match_examples"]
    check(len({e["example_id"] for e in examples}) == len(examples), "duplicate query example")
    for example in examples:
        actual = select_fixture_candidates(document, example)
        check(actual == (example["expected_status"], example["expected_source_ids"]),
              "query example failed: " + example["example_id"])
    for key in [("E02", "RQ1"), ("E04", "RQ1"), ("E04", "RQ2")]:
        group = [e for e in examples if (e["case_id"], e["request_item_id"]) == key]
        check(sum(e["expected_status"] == "eligible" for e in group) >= 2,
              "missing synonym positives")
        check({"wrong_topic", "collection_mismatch"} <= {e["expected_status"] for e in group},
              "missing wrong topic/collection negatives")
    # Prove ID examples are not a hidden whitelist, without changing the corpus.
    narrowed = copy.deepcopy(document)
    e05 = next(b for b in narrowed["request_bindings"] if b["case_id"] == "E05")
    e05["success_source_ids"] = ["SYNTHETIC-X-COUGH"]
    for name in ["E05-guideline", "E05-research", "E05-external"]:
        example = next(e for e in examples if e["example_id"] == name)
        check(select_fixture_candidates(narrowed, example) ==
              (example["expected_status"], example["expected_source_ids"]),
              "E05 example IDs treated as whitelist")


def validate_bundle(bundle, schema):
    for filename, definition in DOCS.items():
        validate_shape(bundle[filename], schema["$defs"][definition], schema, filename)
    originals = original_rows()
    counts = validate_cases(bundle["cases.json"], originals)
    validate_corpus(bundle["retrieval_corpus.json"], bundle["cases.json"]["cases"])
    cases = {c["case_id"]: c for c in bundle["cases.json"]["cases"]}
    variants = bundle["fault_variants.json"]["variants"]
    check(len({v["variant_id"] for v in variants}) == len(variants), "duplicate fault variant")
    check(all(v["base_case_id"] in cases for v in variants), "invalid fault base case")
    for p in bundle["provenance.json"]["source_documents"]:
        check(
            hashlib.sha256((ROOT / p["path"]).read_bytes()).hexdigest() == p["sha256"],
            "original report hash changed",
        )
    for change in bundle["wording_changes.json"]["changes"]:
        check(change["case_id"] in cases and change["before"] != change["after"], "empty change")
    for c in cases.values():
        for field in ("description", "question"):
            before = c["original_design"]["original_" + field]
            changes = [
                x
                for x in bundle["wording_changes.json"]["changes"]
                if x["case_id"] == c["case_id"] and x["field"] == field and x["applied"]
            ]
            for x in changes:
                check(x["before"] == before, "non-contiguous wording change chain")
                before = x["after"]
            check(before == c[field], "unrecorded input edit")
    return counts


def negative_checks(bundle, schema):
    def offset(b):
        b["cases.json"]["cases"][0]["source_mappings"][0]["start"] += 1

    def priority(b):
        b["cases.json"]["cases"][7]["expected_request_spec"]["items"][1]["required"] = True

    def contract(b):
        b["cases.json"]["cases"][0]["expected_answer_contract"]["must_cover"] = ["TREATMENT_PLAN"]

    def category(b):
        b["retrieval_corpus.json"]["source_records"][0]["metadata"]["source_kind"] = "research"

    def history(b):
        b["cases.json"]["cases"][-1]["session_layout"]["sessions"]["A"] = [
            {"role": "user", "content": "头痛伴恶心。"}
        ]

    def dependency(b):
        b["cases.json"]["cases"][11]["expected_dependencies"] = []

    def original(b):
        b["cases.json"]["cases"][0]["original_design"]["original_input"] = "altered"

    def synthetic(b):
        b["retrieval_corpus.json"]["source_records"][0]["metadata"]["synthetic"] = False

    checks = [offset, priority, contract, category, history, dependency, original, synthetic]
    for mutate in checks:
        changed = copy.deepcopy(bundle)
        mutate(changed)
        try:
            validate_bundle(changed, schema)
        except (ValueError, KeyError, TypeError):
            continue
        raise ValueError("negative mutation accepted: " + mutate.__name__)
    return len(checks)


def file_hashes():
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(HERE.iterdir())
        if p.is_file() and p.name not in {"manifest.json", "SHA256SUMS.txt"}
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write-manifest",
        action="store_true",
        help="refresh candidate hashes after successful validation; never freeze",
    )
    args = parser.parse_args()
    schema = read_json(HERE / "schema.json")
    bundle = {name: read_json(HERE / name) for name in DOCS}
    counts = validate_bundle(bundle, schema)
    negative = negative_checks(bundle, schema)
    hashes = file_hashes()
    if args.write_manifest:
        manifest = {
            "fixture_version": "w7-2b-v1-candidate",
            "approval_status": "CANDIDATE_PENDING_HUMAN_APPROVAL",
            "frozen": False,
            "case_count": 24,
            "category_counts": counts,
            "validation": "PASS_STATIC_ONLY_NO_MODEL_RUNS",
            "negative_mutations_rejected": negative,
            "query_match_examples_passed": len(
                bundle["retrieval_corpus.json"]["query_match_examples"]),
            "e05_non_whitelist_checks_passed": 3,
            "selection_rules_version": bundle["retrieval_corpus.json"]["query_rules"]["version"],
            "files": hashes,
        }
        (HERE / "manifest.json").write_bytes(
            (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        )
        all_hashes = {
            **hashes,
            "manifest.json": hashlib.sha256((HERE / "manifest.json").read_bytes()).hexdigest(),
        }
        checksums = "".join(f"{digest}  {name}\n" for name, digest in sorted(all_hashes.items()))
        (HERE / "SHA256SUMS.txt").write_bytes(checksums.encode("utf-8"))
    else:
        manifest = read_json(HERE / "manifest.json")
        check(manifest["files"] == hashes, "candidate manifest drift")
        check(manifest["frozen"] is False, "candidate incorrectly marked frozen")
        check(manifest["approval_status"] == "CANDIDATE_PENDING_HUMAN_APPROVAL",
              "candidate incorrectly approved")
        listed = set()
        for line in (HERE / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
            digest, name = line.split("  ", 1)
            check(Path(name).name == name, "unsafe checksum path")
            check(name not in listed, "duplicate checksum entry")
            listed.add(name)
            check(
                hashlib.sha256((HERE / name).read_bytes()).hexdigest() == digest,
                "checksum mismatch: " + name,
            )
        check(listed == set(hashes) | {"manifest.json"}, "incomplete checksum inventory")
    print(
        json.dumps(
            {
                "status": "PASS_STATIC_ONLY",
                "cases": 24,
                "categories": counts,
                "rq_count": sum(
                    len(c["expected_request_spec"]["items"]) for c in bundle["cases.json"]["cases"]
                ),
                "source_records": len(bundle["retrieval_corpus.json"]["source_records"]),
                "retrieval_variants": len(bundle["retrieval_corpus.json"]["retrieval_cases"]),
                "fault_variants": len(bundle["fault_variants.json"]["variants"]),
                "negative_mutations_rejected": negative,
                "query_match_examples_passed": len(
                    bundle["retrieval_corpus.json"]["query_match_examples"]),
                "e05_non_whitelist_checks_passed": 3,
                "frozen": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
