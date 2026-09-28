# Benchmark

## Scope

This release reports two frozen development evaluations:

1. a 60-case execution reliability benchmark;
2. a 60-case matched comparison with DeepSeek Web.

The cases, candidate answer dumps, judge inputs, and local traces are not distributed. This avoids publishing data without clear redistribution permission and keeps the repository free of potentially sensitive clinical material.

## Reliability benchmark

| Metric | Result |
| --- | ---: |
| Total fixed cases | 60 |
| Completed | 60 / 60 |
| Non-empty final answers | 60 / 60 |
| Tool replay | 0 |
| Protocol recovery | Validated |

The reported set contains one valid execution per ordered case. A Provider attempt is considered infrastructure-invalid only when it returns no response content and the trace classifies the failure as a transient transport, rate-limit, or server exception. Such an attempt may be repeated with the identical frozen input and configuration. Once a valid model response is received, no rerun is allowed for answer quality. No failed answer was manually repaired or removed after a valid response.

This infrastructure-validity rule does not change the separately frozen clinical-quality comparison below or its judge scores.

## Clinical-quality comparison

All 60 cases were matched one-to-one. DeepSeek Web served as a baseline of saved product outputs collected before evaluation. Candidate answers were frozen before evaluation and were not edited or regenerated. The rubric asked which answer was more useful and reliable for clinical decision support, using medical correctness, completeness, clinical workflow, and safety/uncertainty handling. Writing style and answer length were explicitly excluded as winning criteria.

### Model control / system-comparison scope

Both MedAgent and the DeepSeek Web baseline used the same underlying DeepSeek model. This removes foundation-model identity as the primary difference between the evaluated systems and makes the fixed paired 60-case comparison more informative about system-level orchestration, context handling, execution control, and verification. However, DeepSeek Web is a closed product surface: its system prompts, serving parameters, context policies, routing, search/tool behavior, and other internal controls are not observable. The benchmark is therefore reported as a paired complete-system comparison, not a strict causal ablation of individual Harness components, and the observed **+6.49%** delta is not attributed solely to the Harness.

| Metric | MedAgent | DeepSeek Web | Delta |
| --- | ---: | ---: | ---: |
| Overall Clinical Quality | 4.6733 | 4.3883 | +0.2850 |
| Medical Correctness | 4.8333 | 4.3333 | +0.5000 |
| Clinical Completeness | 4.7833 | 4.0667 | +0.7166 |
| Clinical Workflow | 4.7833 | 4.0833 | +0.7000 |
| Safety / Uncertainty | 4.8833 | 4.4833 | +0.4000 |

Winner counts:

| Outcome | Cases |
| --- | ---: |
| MedAgent | 52 |
| DeepSeek Web | 8 |
| Tie | 0 |

MedAgent's Overall Clinical Agent Score (OCAS) was 4.6733 versus 4.3883 for DeepSeek Web, a difference of +0.2850 (+6.49%). In this repository, OCAS / Overall Clinical Quality is a project-defined composite evaluation criterion; it is not presented as an official MedCli score or as an independently standardized clinical metric. The main observed advantages were requirement coverage, structured clinical workflow, and safety-aware reasoning.

## Interpretation limits

- This is a fixed development benchmark, not an unseen test set.
- It is not an independent clinical trial or clinical validation.
- The sample is too small and narrow for claims of general medical superiority.
- Model-judge scores can contain calibration and position bias.
- The results should be reproduced on independently governed held-out data before deployment decisions.

The supported conclusion is: **MedAgent achieved higher overall clinical decision-support quality on this fixed 60-case comparison.**
