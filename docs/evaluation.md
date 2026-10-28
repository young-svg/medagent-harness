# Evaluation

## Historical predecessor baseline (unversioned record)

The original tracked note reports the following for a predecessor design baseline:
20/20 execution success, 90% exact routing accuracy, 97.22% worker micro-F1,
100% dispatchable plans, and no timeout or generation failures in that small
development set.

The tracked note does **not** identify an exact predecessor commit, provider/model
revision, case-set hash, or judge configuration. Its evaluation version must therefore
be cited as **pre-native historical design baseline, exact version unrecorded**;
do not relabel it as current main, v6, v7, or an A1-C1 result. The source outputs are
not bundled here, so this document does not independently substantiate those numbers.

## Current local main: engineering evidence, not new clinical scores

A1-C1 add argument validation, final structural coverage/recovery, explicit Trace
causality, subtask-scoped RAG context, estimated Memory budgets, and dependency-aware
Handoff. Offline tests exercise those boundaries; they do not establish answer quality,
medical effectiveness, or a score improvement. See the README's implementation/test map.

`ORCHESTRATION_EFFICIENCY_V7.md` describes an earlier policy version, not a fresh clinical
evaluation. `MEDICALQA_NATIVE_RAG_INTEGRATION.md` records an older retrieval-only smoke,
not current end-to-end model performance. Untracked/local evaluation artifacts and résumé
figures are not evidence for this documentation update and have not been imported.

No real-provider or clinical benchmark rerun is claimed for the A1-C1 main revision.
To publish a future score, retain the production commit, exact model/provider settings,
licensed case-set/version/hash, selection procedure, baseline configuration, judge/rubric
and version, and per-case outcomes with errors, latency and cost. Distinguish execution
completion, structural coverage, retrieval relevance, and medical-answer grading.
Publish only authorized, de-identified aggregate evidence; keep raw sensitive traces private.
