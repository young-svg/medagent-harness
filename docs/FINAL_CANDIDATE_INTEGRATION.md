# Final Candidate Integration

## Versions and isolation

- Base: Presentation V3 commit `1a02985d9a95709daa5b6c9f6ef776ddb64ae504`.
- Runtime source commit: `118c82d628cfefcd2477728e428ce92c3ececf07` (`Fix deliverable coverage after plan pruning`). Cherry-picked unchanged onto the isolated `final-candidate` branch as `fc98a9cb5d997c4f9f9bba7366313e479471ea01`.
- Final candidate: this report's committed branch `HEAD` (`git rev-parse HEAD`). Its exact SHA is supplied in the final task handoff; a commit cannot contain its own SHA without changing that SHA.
- The dirty main worktree and the dirty `presentation-v3` worktree were not modified. No artifacts, screenshots, traces, local database, cache, or `node_modules` were added to Git.

## Integration method and final source scope

The source `presentation-v3` worktree's status, diff, and diff stat were inspected. Twenty tracked Presentation/Demo source files and four explicitly selected new source/test/docs files were transferred into the clean candidate worktree as a curated, hash-verified file set. The older `PRESENTATION_V3_STABILITY_REPORT.md` and `RUNTIME_STABILITY_FAILURE_TRIAGE.md` remain outside this candidate because they are historical diagnostic records, not integrated source or required product documentation. A short header in the carried-forward Presentation coverage report identifies it as historical; this report is the authoritative integration result.

Changed files relative to the base, grouped by scope:

- Runtime reconciliation: `medagent/planning/planner.py`; `tests/test_plan_deliverable_reconciliation.py`; `docs/RUNTIME_DELIVERABLE_COVERAGE_FIX.md` (the cherry-picked commit, unmodified).
- Presentation protocol and adapter: `medagent/presentation/transform.py`, `medagent/presentation/models.py`, `medagent/presentation/adapter.py`, `medagent/runtime/native_engine.py`; `tests/test_presentation_transform.py`.
- Frontend presentation: `web/src/App.tsx`, `web/src/components/AnswerSummary.tsx`, `ClinicalDetail.tsx`, `DeveloperPanel.tsx`, `PlainLanguageCard.tsx`, `web/src/presentationAdapter.ts`, `web/src/styles.css`, `web/src/types.ts`.
- Local Demo source and tests: `examples/demo_cases/README.md`, `demo_01_simple_single.json` through `demo_04_memory_followup.json`, `web/src/demoData.ts`, `web/src/demoFinalAnswers.json`, `web/src/demoEvidenceCards.json`, `tests/test_demo_v3_fixtures.py`.
- Documentation: `docs/DEMO_CASES.md`, `docs/PRESENTATION_V3_FINAL_COVERAGE_FIX.md`, and this report.

No Planner prompt, Worker, RAG backend, memory, retry, model/provider selection, Contract Gate, benchmark, or Demo-external runtime behavior was modified as part of this integration. The only `native_engine.py` delta forwards RequestSpec to Presentation and traces its result.

## Automated and deterministic regression

| Check | Result |
| --- | --- |
| Full `pytest` | **PASS, 159 tests**. Two dependency deprecation warnings only. |
| Targeted Runtime ownership + Presentation coverage + Demo fixture tests | **PASS, 39 tests**. |
| `ruff check medagent tests` | **PASS**. |
| `npm run build` | **PASS**; Vite production build emitted `dist/index.html`, CSS and JS assets. The first sandboxed invocation hit Windows `spawn EPERM`; the same build passed with normal process permissions. |
| `git diff --check` | **PASS**. JSON fixture line-ending normalization warnings are not diff errors. |

Key assertions: the historical Research-subtask pruning shape reassigns `FURTHER_TESTS` to a surviving clinical owner; single-Worker and Planner fallback plans own all required deliverables; failed Workers remain missing at the unchanged Contract Gate; complete plans are idempotent. Presentation accepts all required `RQ1`–`RQ4` Direct sections, rejects missing `RQ2`/`RQ4` for its existing fallback path, and preserves the professional `final_answer` exactly. These checks are deterministic and use no provider calls.

## Demo-02 browser acceptance

Only the local `demo-02-multi-agent` fixture was selected in the final-candidate frontend at `http://127.0.0.1:5175/`. **PASS**:

- `/api/analyze` requests: **0**.
- Direct layer shows four visible topics: 最可能诊断、主要鉴别诊断、进一步检查、治疗与随访.
- Simple Why explains why progressive difficulty swallowing, weight loss, anemia, and occult blood are concerning, and why gastroscopy, tissue diagnosis, and staging guide care.
- Clinical Detail displayed **1,957 characters**, an exact text match to the completed source run's full professional `final_answer` fixture, not the former short summary.
- The three inner answer cards have no 01/02/03 prefixes; only top-level Input `01` and Analysis `02` remain.
- Developer Mode visibly says `DEMO FIXTURE`.

Screenshot (not committed): `E:/agent/medagent-harness/artifacts/final_candidate/demo02_final.png`. Browser assertion record (not committed): `E:/agent/medagent-harness/artifacts/final_candidate/demo02_browser_result.json`.

## One live esophageal case

Exactly one real-provider submission was made against the final-candidate backend code, using the specified 58-year-old man with year-long reflux, progressive solid-food dysphagia, 5 kg loss, Hb 105 g/L, positive fecal occult blood, stable vital signs, no prior gastroscopy, and the four-part diagnosis/differential/tests/treatment-follow-up question. Run ID: `0c543df5-5d0e-4b57-ac66-04dc538e680b`. This case did not request external evidence; retrieval mode was `off` for this local run. No RAG behavior is claimed from it.

| Check | Observed result |
| --- | --- |
| Agent status | **completed**; final professional answer nonempty, 2,047 characters |
| RequestSpec | One required compound item `RQ1` containing the complete four-part original question |
| AnswerContract required | `TREATMENT_PLAN`, `FURTHER_TESTS`, `DIFFERENTIAL_DIAGNOSIS`, `DIAGNOSIS_WITH_BASIS` |
| Route / Workers | `multi`; `diagnostic_agent/ST1` and `consultation_agent/ST4`, both successful, no failed Worker |
| Final plan ownership | `ST1`: diagnosis, differential, tests; `ST4`: treatment. Required owner union: **4/4**. No pruning repair was needed in this particular Planner output; the historical pruning shape is covered by deterministic tests. |
| Completion Gate | Contract covered **4/4**, missing none; RequestSpec `RQ1` covered, missing none; `contract_complete=true`, `user_request_complete=true` |
| Presentation | `status=success`, `parse_status=valid`, provider `finish_reason=stop`; required and Direct-covered IDs both `[RQ1]`. Its single Direct section explicitly includes diagnosis, differentials, further tests, treatment, and follow-up. |
| Professional answer preservation | `presentation.professional_answer == final_answer`: **true**, exact string equality |
| Infrastructure | No provider error, no Worker infrastructure retry, no Planner fallback |

The single compound `RQ1` means ID-level Presentation coverage alone is not a four-ID proof; the Direct section content was also inspected for all four requested categories. The live result is **PASS**. The full returned payload and sanitized trace are retained only under `E:/agent/medagent-harness/artifacts/final_candidate/` and are not committed.

## Decision

Automated tests, deterministic regressions, Demo-02 browser acceptance, and the one permitted live case all passed. No deterministic logic blocker or external infrastructure error was observed in the final live run. The candidate is suitable for manual acceptance. No COMPOSITE-20, Judge, DeepSeek comparison, additional live retry, or GitHub push was performed.
