"""Read-only public subset checks; never claim validation of archived originals."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "frozen_v1"
REPO = HERE.parents[2]
ORIGINAL_MANIFEST_SHA = "33adc420b2b7e5e8117084954439daad8dba12700f241858a38c3e237f18b1cd"
ARCHIVED = {
    "reference/W7-1_original_design_report.md",
    "reference/W7-1_24_case_draft_original.md",
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory():
    full_path = DATA / "freeze/manifest.json"
    require(digest(full_path) == ORIGINAL_MANIFEST_SHA, "original manifest changed")
    full = json.loads(full_path.read_text(encoding="utf-8"))
    public = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
    expected = {p: h for p, h in full["files"].items() if p not in ARCHIVED}
    expected["freeze/manifest.json"] = ORIGINAL_MANIFEST_SHA
    # The immutable checksum file still describes the full internal archive.
    checksums = {}
    for line in (DATA / "freeze/SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
        value, name = line.split("  ", 1)
        require(name not in checksums, "duplicate original checksum")
        checksums[name] = value
    require(checksums == {**full["files"], "freeze/manifest.json": ORIGINAL_MANIFEST_SHA},
            "original checksum inventory changed")
    expected["freeze/SHA256SUMS.txt"] = digest(DATA / "freeze/SHA256SUMS.txt")
    require(public["original_manifest_sha256"] == ORIGINAL_MANIFEST_SHA, "wrong source snapshot")
    require(public["included_files"] == expected and len(expected) == 18, "wrong public inventory")
    require(public["archived_originals"] == {p: full["files"][p] for p in ARCHIVED},
            "wrong archive declarations")
    require(public["source_document_verification"] == "NOT_PERFORMED_IN_PUBLIC_PACKAGE",
            "must not claim original-source verification")
    actual = {p.relative_to(DATA).as_posix() for p in DATA.rglob("*")
              if p.is_file() and "__pycache__" not in p.parts}
    require(actual == set(expected), "unexpected or missing public frozen file")
    for name, value in expected.items():
        require(not Path(name).is_absolute() and ".." not in Path(name).parts, "unsafe path")
        require(digest(DATA / name) == value, "changed public byte content: " + name)
    return public


def load_checker():
    sys.path.insert(0, str(DATA))
    sys.path.insert(0, str(REPO))
    spec = importlib.util.spec_from_file_location(
        "_w7_immutable_public_checks", DATA / "validate_candidates.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_bundle(checker, bundle, schema, approved_rows):
    # Use existing semantic checks against approved embedded annotations. This
    # is internal consistency, NOT a substitute for reading the two originals.
    for filename, definition in checker.DOCS.items():
        checker.validate_shape(bundle[filename], schema["$defs"][definition], schema, filename)
    counts = checker.validate_cases(bundle["cases.json"], approved_rows)
    checker.validate_corpus(bundle["retrieval_corpus.json"], bundle["cases.json"]["cases"])
    cases = {c["case_id"]: c for c in bundle["cases.json"]["cases"]}
    variants = bundle["fault_variants.json"]["variants"]
    require(len({v["variant_id"] for v in variants}) == len(variants), "duplicate variant")
    require(all(v["base_case_id"] in cases for v in variants), "unknown variant case")
    for change in bundle["wording_changes.json"]["changes"]:
        require(change["case_id"] in cases and change["before"] != change["after"],
                "invalid wording change")
    for case in cases.values():
        for field in ("description", "question"):
            value = case["original_design"]["original_" + field]
            for change in bundle["wording_changes.json"]["changes"]:
                if (change["case_id"] == case["case_id"] and change["field"] == field
                        and change["applied"]):
                    require(change["before"] == value, "non-contiguous wording edits")
                    value = change["after"]
            require(value == case[field], "unrecorded input edit")
    return counts


def mutations(checker, bundle, schema, approved_rows):
    changes = [
        lambda b: b["cases.json"]["cases"][0]["source_mappings"][0].__setitem__("start", 1),
        lambda b: b["cases.json"]["cases"][7]["expected_request_spec"]["items"][1]
        .__setitem__("required", True),
        lambda b: b["cases.json"]["cases"][0]["expected_answer_contract"]
        .__setitem__("must_cover", ["TREATMENT_PLAN"]),
        lambda b: b["retrieval_corpus.json"]["source_records"][0]["metadata"]
        .__setitem__("source_kind", "research"),
        lambda b: b["cases.json"]["cases"][-1]["session_layout"]["sessions"]
        .__setitem__("A", [{"role": "user", "content": "SYNTHETIC changed history"}]),
        lambda b: b["cases.json"]["cases"][11].__setitem__("expected_dependencies", []),
        lambda b: b["cases.json"]["cases"][0]["original_design"]
        .__setitem__("original_input", "altered"),
        lambda b: b["retrieval_corpus.json"]["source_records"][0]["metadata"]
        .__setitem__("synthetic", False),
    ]
    for change in changes:
        altered = copy.deepcopy(bundle)
        change(altered)
        try:
            validate_bundle(checker, altered, schema, approved_rows)
        except (ValueError, KeyError, TypeError):
            continue
        raise ValueError("public mutation was accepted")
    return len(changes)


def main():
    require(len(sys.argv) == 1, "read-only validator accepts no write or archive options")
    public = inventory()
    checker = load_checker()
    schema = checker.read_json(DATA / "schema.json")
    bundle = {name: checker.read_json(DATA / name) for name in checker.DOCS}
    # Source bytes have been pinned above, before extracting the approved rows.
    rows = {c["case_id"]: copy.deepcopy(c["original_design"]["table_columns"])
            for c in bundle["cases.json"]["cases"]}
    counts = validate_bundle(checker, bundle, schema, rows)
    rejected = mutations(checker, bundle, schema, rows)
    inventory()
    print(json.dumps({
        "status": "PASS_PUBLIC_SUBSET_ONLY",
        "public_files": len(public["included_files"]),
        "archived_originals": len(public["archived_originals"]),
        "source_document_verification": "NOT_PERFORMED",
        "cases": len(bundle["cases.json"]["cases"]),
        "request_items": sum(len(c["expected_request_spec"]["items"])
                             for c in bundle["cases.json"]["cases"]),
        "categories": counts,
        "query_examples": len(bundle["retrieval_corpus.json"]["query_match_examples"]),
        "e05_checks": 3, "mutations_rejected": rejected,
        "runtime_or_model_executed": False,
    }, indent=2))


if __name__ == "__main__":
    main()
