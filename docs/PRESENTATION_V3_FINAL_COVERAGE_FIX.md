# Presentation V3 Final Coverage Fix — Acceptance Record

> Historical source-worktree acceptance record. Its uncommitted/incomplete status describes the earlier Presentation-only trial, before Runtime deliverable reconciliation. See `FINAL_CANDIDATE_INTEGRATION.md` for the integrated candidate outcome.

Baseline: `presentation-v3` at `1a02985d9a95709daa5b6c9f6ef776ddb64ae504`.

Status: **not fully accepted; no commit created**. The Demo and deterministic Presentation checks pass. The requested live esophageal-case acceptance did not reach a completed final answer on the final browser run, so live direct-layer coverage cannot honestly be reported as 100%.

## Root cause and design

Previously the Presentation transform received only case, question, and professional `final_answer`. It had no required RequestSpec item list; a short Direct layer could omit explicit deliverables without a detectable protocol error. Demo-02 compounded this with a hand-shortened Clinical Detail and a three-item Direct layer that did not explicitly answer diagnosis, differential, tests, and treatment/follow-up. The Analysis cards also repeated the page's 01/02 numbering.

The transform now receives **only required** RequestSpec items (`id`, `text`, `semantic_type`) in addition to its previous inputs. It does not receive internal trace or hidden reasoning. The prompt requires one `<DIRECT_SECTION id="RQn" title="...">` per required item, with concrete bullet content. A deterministic parser rejects missing, duplicate, extra, empty, or malformed sections; rejection follows the existing Presentation fallback path and does not change Agent completion. Matching optional closing tags are accepted conservatively because a captured real response contained them. The response preserves structured Direct sections for the frontend; `professional_answer`/Clinical Detail remains the exact final answer. No second Presentation call or quality retry was added.

This check guarantees **structural coverage by required RequestSpec ID**, not independent medical or semantic correctness. When a compound unnumbered question is extracted as a single RQ, the check cannot prove each sub-deliverable separately without changing RequestSpec extraction, which was explicitly out of scope.

## Demo fixture update

All four local Demo Clinical Detail values are exact completed real Agent `final_answer` strings, identified by `source_run_id` in the JSON fixtures and kept in sync with `web/src/demoFinalAnswers.json`. Their lengths are 1165, 1957, 1637, and 1121 characters respectively. The professional text is not hand summarized. Direct and Why are concise presentation text derived from the corresponding answer; Developer workflow remains explicitly illustrative fixture metadata.

| Demo | Source run | Direct layer | Clinical Detail |
| --- | --- | --- | --- |
| 01 Single | `e9c126e5-f086-4805-a9db-58d9c7a96116` | Diagnosis, basis, tests, initial management | 1165 chars, exact final |
| 02 Multi | `eccbee9f-4cfe-4b29-b550-4364695e8387` | Diagnosis, differential, tests, treatment/follow-up | 1957 chars, exact final |
| 03 RAG | `15e4cfb1-6901-4cbd-ad54-4df0d4a62def` | Hypertension management and retrieved basis | 1637 chars, exact final |
| 04 Memory | `41aa51be-245b-4ef7-80de-af132a36e98b` | Prior context, prevention, follow-up | 1121 chars, exact final |

Demo-03 now uses a hypertension case with six real local MedicalQA-DX Milvus Evidence Cards from its source run; the previous synthetic postoperative evidence and user-facing engineering text were removed. This is still a static local Demo: selecting it performs no retrieval or API call. Corpus provenance and redistribution permission remain to be confirmed before any public release of evidence previews.

## Demo-02 before and after

Before: “优先排除占位性病变 / 尽快胃镜 / 复核贫血” and a short three-paragraph Clinical Detail. After: four visible Direct sections explicitly state the most likely malignant diagnosis (pending pathology), key differentials, endoscopy/biopsy and conditional staging, and treatment/follow-up based on pathology and stage. Why explains the alarm symptoms in ordinary language. Clinical Detail is the complete 1957-character professional answer from the completed source run.

The outer page retains `01` input and `02` Analysis. The three inner cards now show unnumbered `Direct Action / Conclusion`, `Simple Why`, and `Clinical Detail`. Demo engineering labels are confined to the badge/Developer Mode/metadata, not the three answer bodies.

## Validation

- `pytest -q`: **150 passed**, two dependency deprecation warnings. Coverage tests include one required RQ, four required RQs, missing RQ2/RQ4 → parser failure/fallback, duplicate ID rejection, placeholder rejection, and exact professional-answer preservation. Demo tests assert four full answers and Demo-02's four deliverables.
- `ruff check medagent tests`: **PASS**.
- `npm run build`: **PASS**.
- `git diff --check`: **PASS**; Git reports only existing line-ending conversion warnings for JSON fixtures.
- Real browser, four local Demos: **4/4 loaded; `/api/analyze` calls = 0**. Demo-02 showed all four Direct headings, two Why paragraphs, 1957-character Clinical Detail, and `DEMO FIXTURE` in Developer Mode. Inner card numbering was absent. Screenshot: `artifacts/three_layer_presentation_v3/demo02_coverage.png`; result: `artifacts/three_layer_presentation_v3/demo_browser_results.json`.

## Live esophageal case — blocker

The same 58-year-old case was submitted in a real browser. In a numbered four-RQ form, run `8d5699d1-dea5-4c22-b2db-2f6ba2e38266` had worker answers for RQ1–RQ4, but the Planner assigned only one Consultation Agent despite `requires_multi_agent=true`; the existing Contract Gate reported missing diagnosis/differential/tests deliverables and returned `incomplete`, with no final answer. This is outside the permitted Presentation-only scope.

The exact original unnumbered question produced a completed source run `eccbee9f-4cfe-4b29-b550-4364695e8387` with a full 1957-character final answer, but its Presentation model response ended for length and correctly used fallback; it cannot prove successful Direct coverage. In the final browser check, run `7e1d6c36-f5c4-41aa-9e2a-255867b276ee` produced `diagnostic_agent` `generation_error` with `failure_reason=invalid_or_empty_worker_response` after two successful provider attempts; the Contract Gate again returned `incomplete` without a final answer. These are separate observed runs, not a quality-retry mechanism. No forbidden Agent, Planner, Worker, generation, RAG, or retry logic was changed.

Therefore: required RequestSpec/Direct coverage is **100% in deterministic tests**, but **not verified at 100% in the requested live browser case**. Professional final-answer preservation is exact in all four Demo fixtures and in completed test/source runs; the final incomplete browser run has no professional final answer to preserve.

Because the live acceptance criterion failed, the requested commit `Align presentation with request coverage` was **not created**. The changes remain uncommitted in the isolated `presentation-v3` worktree; the dirty main worktree was not touched.
