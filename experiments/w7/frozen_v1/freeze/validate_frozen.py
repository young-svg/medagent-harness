"""Read-only verification of approved W7 bytes plus all original static checks."""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parent
REPO = HERE.parents[3]
VERSION = "w7-architecture-evaluation-v1"


def require(value, reason):
    if not value:
        raise ValueError(reason)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_inventory():
    manifest_path = HERE / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(manifest["dataset_version"] == VERSION, "unexpected frozen version")
    require(manifest["status"] == "APPROVED_FROZEN" and manifest["frozen"] is True,
            "dataset is not approved/frozen")
    files = manifest["files"]
    actual = {p.relative_to(DATA).as_posix() for p in DATA.rglob("*")
              if p.is_file() and "__pycache__" not in p.relative_to(DATA).parts}
    require(actual == set(files) | {"freeze/manifest.json", "freeze/SHA256SUMS.txt"},
            "frozen inventory changed")
    for name, expected in files.items():
        require(not Path(name).is_absolute() and ".." not in Path(name).parts,
                "unsafe frozen path")
        require(re.fullmatch(r"[0-9a-f]{64}", expected) is not None, "invalid digest")
        require(digest(DATA / name) == expected, "frozen file changed: " + name)
    approved = manifest["approved_candidate_files"]
    require(len(approved) == 14, "approved snapshot must contain 14 files")
    for name, expected in approved.items():
        require(Path(name).name == name, "unsafe candidate path")
        require(files[name] == expected, "approved snapshot changed: " + name)
    require(approved["cases.json"] ==
            "be12cecebc20250415874f616a955353b06c8cfc391e9dc62514be130f097305",
            "approved cases differ")
    require(approved["SCORING.md"] ==
            "a7437108263e7d909253d1eaf43e01a8eca1370facf06b6b86ad1ff03565a425",
            "approved rubric differs")
    listed = {}
    for line in (HERE / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
        expected, name = line.split("  ", 1)
        require(name not in listed, "duplicate frozen checksum")
        listed[name] = expected
    require(listed == {**files, "freeze/manifest.json": digest(manifest_path)},
            "frozen checksum inventory mismatch")
    return manifest


def validate_snapshot():
    # Load the exact reviewed validator without editing its bytes. Only redirect
    # its original-document location to the bundled, checksummed reference copy.
    sys.path.insert(0, str(DATA))
    sys.path.insert(0, str(REPO))
    spec = importlib.util.spec_from_file_location(
        "_w7_frozen_v1_static_checks", DATA / "validate_candidates.py",
    )
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    checker.ROOT = DATA / "reference"
    capture = io.StringIO()
    with contextlib.redirect_stdout(capture):
        checker.main()
    result = json.loads(capture.getvalue())
    expected = {"cases": 24, "rq_count": 40, "negative_mutations_rejected": 8,
                "query_match_examples_passed": 29, "e05_non_whitelist_checks_passed": 3,
                "source_records": 9, "retrieval_variants": 36, "fault_variants": 18}
    require(all(result[key] == value for key, value in expected.items()),
            "approved validation totals changed")
    require(result["status"] == "PASS_STATIC_ONLY", "snapshot validation failed")
    require(result["categories"] == {"simple": 4, "independent": 5, "dependent": 5,
                                     "external_evidence": 6, "memory": 4},
            "approved category counts changed")
    return result


def main():
    require(len(sys.argv) == 1, "read-only frozen validation accepts no write options")
    manifest = verify_inventory()
    result = validate_snapshot()
    verify_inventory()
    print(json.dumps({"status": "PASS_FROZEN_STATIC_ONLY", "dataset_version": VERSION,
                      "frozen": True, "files": len(manifest["files"]) + 2,
                      "approved_snapshot_files": 14,
                      "snapshot_checks": result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
