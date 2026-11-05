# Final publication-scope review

> **Published:** [PR #1](https://github.com/young-svg/medagent-harness/pull/1)
> was merged into `release/github-ready` as `ae43019d6c58`.
> This document preserves the pre-release review process. References below to
> BLOCKED, PENDING, authorization outstanding, or no Push/merge describe their
> historical review state, not the current publication status. The original
> conclusions and evidence remain unchanged.

Status: **TECHNICALLY READY TO REQUEST OWNER PUBLICATION AUTHORIZATION**.
This is not publication permission, a new open-source license, clinical validation,
or a certification of every historical artifact. No Push or merge is performed.

## Candidate and clean ancestry

- Branch: `review/medagent-final-release-clean`.
- Base: `origin/release/github-ready` at
  `61471969e187b665e37ee7227bf987c93e6a7bf6`.
- Reviewed content source: the previous local candidate
  `54bd691f3e7c26098097440306725f88f0541a8d`.
- This branch imports selected file snapshots, not the previous candidate or ten
  development commits as ancestors. Neither excluded report is added and then
  deleted. The old candidate and local original files remain internally preserved.
- Runtime/API files are unchanged from the verified candidate. All necessary tests
  remain. The portable SHA-pinned W6 test fixture remains unchanged.
- Delta: 107 retained files from the previous candidate plus three public-packaging
  files, **110 files total**. No core, permission, dependency, rubric or model change.

## Removed original documents and actual dependencies

| Excluded original | Source and preservation | Runtime / test necessity |
| --- | --- | --- |
| `experiments/w7/frozen_v1/reference/W7-1_original_design_report.md` | User-supplied original report; internally retained as `W7-1_original_design_report.md` and in the old local candidate | No runtime/Runner dependency. Required only for full original-document provenance validation and generation history. Contains local absolute source citations at original lines 26–28, 30–32, 34, 36 and 248. |
| `experiments/w7/frozen_v1/reference/W7-1_24_case_draft_original.md` | User-supplied 24-case draft; internally retained as `W7-1_24_case_draft_original.md` and in the old local candidate | No runtime/Runner dependency. Full internal validator compares its table against the report; public execution does not require the full document. |

Original SHA-256 values remain unchanged:
`6707546aeccc9257709c19142aebf7a6f4f6f120f0a312d998a15c830adc281f`
and `69522d08de68cc8ec0eec0c747d80c96c5858ee89f16907755b90b01b8c2ab8f`.
Their provenance names/hashes do not mean their bodies are publicly bundled.

## Minimal packaging adjustment

The public checkout contains **18 byte-identical frozen files**, including all 14
runtime-approved payload files. The two original documents are omitted from the
tree and from newly reachable Git objects. Cases, RQs, Contract, evidence requirements,
scores, rubric, immutable original manifests and original validators are untouched.

[Public packaging instructions](../experiments/w7/public_release/README.md) and
a separate manifest describe the exact subset. The new read-only packaging validator
checks all 18 hashes, existing schema/case/offset/priority/dependency/corpus rules,
29 Query examples, 3 E05 checks and eight mutation types. It reuses approved embedded
annotations for internal consistency, **not fabricated original files**.

The existing integrity regression now invokes this public subset validator and
retains its case hash, frozen-status, scoring, Schema and identity assertions.
No test file, scenario or required acceptance gate was deleted. Its output explicitly
states source-document verification NOT_PERFORMED. Full original-file hash/table
comparison remains an internal archive check; the unchanged full-archive validator
cannot pass in a public checkout missing those originals and is not reported as passing.

Frozen README/provenance/validator files remain historical snapshots with unchanged
hashes. Any old reference to bundled originals describes the internal archive,
not the public package. Current instructions are in the separate public README.
This does not create a different case set or change prior experiment conclusions.

## Remaining concrete owner decisions

These are outstanding authorization decisions documented by
[Public release audit](PUBLIC_RELEASE_AUDIT.md), not findings that the content is
infringing or that an unspecified third party owns it. Technical checks cannot
supply the owner's confirmation.

| Content | Required confirmation / boundary |
| --- | --- |
| Project source and role prompts: `medagent/`, `api/`, `eval/w7/`, `medagent/skills/specs/` | Owner confirms authority to publish, including any actual employer, university or collaborator interest and any imported prompt/code rights. No new such third-party source body was identified in this packaging pass. |
| Approved synthetic evaluation payload: `experiments/w7/frozen_v1/cases.json`, `retrieval_corpus.json` and rubric | Dataset-design approval is not by itself redistribution clearance. Retained embedded case annotations derive from the supplied design; omitting the full reports does not grant rights to those retained annotations. |
| Demo narratives/answers: `web/src/demoData.ts`, `web/src/demoFinalAnswers.json`, `examples/demo_cases/` | Confirm authorship/provenance and permission to publish the retained text. These existing files are unchanged, and their synthetic labels are not an independent rights audit. |
| Historical comparison: `docs/BENCHMARK.md`, `docs/RELIABILITY.md` and underlying saved baseline material | Confirm permission for aggregate claims, source material and product/model references. Results are not modified or regraded. |
| Existing release ancestry: old `artifacts/integration-trace/`, `examples/sample_trace/`, screenshots/logs and `eval/requestspec_validation/` excerpts | Already reachable from the public release base, not newly copied. Existing history disclosure/redistribution decisions remain with the owner; the user prohibited rewriting release history. No claim of sanitized history is made. |

The two omitted full reports no longer require a redistribution decision for this
candidate's new objects. If later restored, they require a separate explicit review.
Third-party dependencies retain their own declared licenses; no new dependency,
bundled third-party source, binary distribution or project license is introduced.
The repository remains public-viewing/evaluation-only, without an open-source grant.

## Verification

Current clean-candidate minimal regression:

- **986 passed in 35.70s**, no failures, skips or removed tests.
- Ruff: all 132 base/candidate Python files passed; git diff --check passed.
- Public subset: 18 original byte hashes, 24 cases / 40 RQs, categories 4/5/5/6/4,
  29 Query examples, 3 E05 checks and eight mutation types passed. Original-document
  source verification is explicitly NOT_PERFORMED in the public package.
- 76 document-relative links resolve. The SVG is unchanged and valid XML.
- All 37 core/API snapshots and 18 retained frozen files are byte-identical to the
  previous verified candidate. Both local original files retain their original SHA.
- Windows tests use a fresh external temporary tree and the same process-local
  mkdir-mode compatibility; no old permissions or product settings were changed.
- No model experiment, live Provider request or new offline experimental batch was
  run. Prior Smoke/build/experiment results remain historical, not new results.

Independent single-branch clone of content commit
`8c919457fbde34a65ccfc3a6b12fb32e190ac1a8`:

- Public subset validation passed without either original document present.
- Seven integrity/default-analyze regression cases passed in 1.67s; Ruff passed.
- Neither excluded original blob exists in the independent clone's object store;
  the previous candidate commit is absent as well. The clone was created without
  local object sharing or hardlinks.
- Both excluded filenames and blob IDs are absent from the candidate's entire
  reachable object inventory. The initial content commit has the release base as
  its direct parent, with no development/candidate ancestry imported.

No publication authority is inferred from these checks.

## Exact change scope relative to the prior candidate

- Omit only the two full originals listed above.
- Add `experiments/w7/public_release/{README.md,manifest.json,validate_public_subset.py}`.
- Adapt `tests/test_w7_runner.py`'s one integrity-test entry to public packaging;
  preserve and extend its assertions.
- Update README and architecture/evaluation documentation to point to public subset
  instructions and internal archive boundaries.
- Keep every remaining frozen file's byte hash, all runtime/API code, frontend,
  Pages workflow, dependency configuration and historical results unchanged.

The full file selection is the previous candidate's reviewed 109-file delta, minus
the two originals, plus the three explicit packaging files. No historical untracked
directory, credential, real answer, Trace, cache or temporary output is included.
