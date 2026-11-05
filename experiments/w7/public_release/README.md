# W7 public packaging subset

This checkout includes **18 byte-identical files** from the approved 20-file frozen
archive. It deliberately omits the two user-supplied original design reports:

- `frozen_v1/reference/W7-1_original_design_report.md`
- `frozen_v1/reference/W7-1_24_case_draft_original.md`

Their original bytes, SHA-256 values and the full source archive remain internally
preserved. They are not publicly bundled or fetched by this package. Their names
and hashes in historical provenance are metadata, not links to published originals.
The omitted full report contained machine-local code citations; neither its body
nor the old candidate commits are needed by the runtime or Runner.

`manifest.json` outside the frozen directory declares the exact public subset.
The original frozen manifests, checksums, case inputs, RQs, Contract, rubric and
selection rules are unchanged. This is a packaging subset, not a newly approved
dataset, a refreshed fixture version or a claim of document redistribution rights.

Run from the repository root:

```bash
python experiments/w7/public_release/validate_public_subset.py
```

This verifies all 18 public byte hashes against the pinned original manifest,
uses the existing schema/case/offset/priority/dependency/corpus checks against the
approved embedded annotations, checks 29 Query examples and 3 E05 cases, and rejects
the same eight mutation types. It does not invoke an Agent, model, tool or service.
It explicitly reports `PASS_PUBLIC_SUBSET_ONLY` and source-document verification
`NOT_PERFORMED`; it does not reconstruct or fake the missing originals.

The immutable `frozen_v1/README.md`, `freeze/README.md` and full-archive validator
are historical snapshot files. Their references to bundled originals and their
original commands describe the internal 20-file archive, **not this public subset**.
Running `frozen_v1/freeze/validate_frozen.py` in this checkout will fail on the missing
originals by design. Full comparison to the original report table and original-file
hashes remains an internal-archive check. No test suppresses or relabels that check
as a public success. The public regression invokes the subset check instead and
retains its case/rubric/scoring assertions.

Publication was completed through [PR #1](https://github.com/young-svg/medagent-harness/pull/1)
within the owner's confirmed public scope; see the historical
[release review](../../../docs/FINAL_RELEASE_REVIEW.md). Retained synthetic cases,
retrieval materials and scoring materials remain subject to the existing
All Rights Reserved / No Open-Source License statement. The two original design
reports remain non-public. Public subset validation does not establish provenance
against those original documents or grant a license to the retained code or fixtures.
