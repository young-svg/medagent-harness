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
| Original single-pass completion | 59 / 60 |
| Targeted infrastructure rerun | MED-057 completed |
| Composite completion | 60 / 60 |
| Composite non-empty final answers | 60 / 60 |
| Tool replay | 0 |
| Protocol recovery | Validated |

The original run used one normal execution per ordered case and remains preserved at 59/60. Its only incomplete case, `MED-057`, received no Provider content because the initial worker request and its one bounded retry both ended in `ConnectError`. After infrastructure retry hardening, only that case was rerun once with the identical frozen input and model configuration; it completed on its first Provider attempt. The reported 60/60 reliability value is therefore a composite of 59 unchanged original successes plus the targeted `MED-057` result—not a new single-pass 60-case run and not a quality-triggered regeneration.

The targeted reliability rerun does not change the separately frozen clinical-quality comparison below or its judge scores.

## Clinical-quality comparison

All 60 cases were matched one-to-one. DeepSeek Web served as a baseline of saved product outputs collected before evaluation. Candidate answers were frozen before evaluation and were not edited or regenerated. The rubric asked which answer was more useful and reliable for clinical decision support, using medical correctness, completeness, clinical workflow, and safety/uncertainty handling. Writing style and answer length were explicitly excluded as winning criteria.

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
- The comparison measures the complete systems on these cases, not isolated model intelligence.
- The results should be reproduced on independently governed held-out data before deployment decisions.

The supported conclusion is: **MedAgent achieved higher overall clinical decision-support quality on this fixed 60-case comparison.**
