# Reliability

## Reliability objective

The runtime treats completion as a control-flow and coverage property. A provider response is not considered successful merely because it is non-empty.

## Fixed 60-case result

These are the existing pre-A1-C1 release results, not a rerun of the integrated
runtime. Offline regressions verify engineering behavior, not clinical efficacy.

| Metric | Result |
| --- | ---: |
| Cases | 60 |
| Completed | 60 |
| Incomplete | 0 |
| Non-empty final answers | 60 |
| Tool replay during protocol recovery | 0 |
| Protocol recovery path | Validated |

The result set contains one valid execution for each fixed case. An attempt with no Provider response content is excluded only when its trace classifies the cause as a transient infrastructure exception. The case is then repeated with the identical frozen input and runtime configuration. A valid model response is never repeated for answer quality, and completion still requires request and contract coverage.

## Reliability mechanisms

### Request and contract coverage

Each required request item and clinical deliverable must first be backed by a
valid non-empty Worker answer. Final per-item coverage is then checked after
synthesis, deterministic recovery and safety edits. Worker coverage or Planner
assignment alone does not establish final completion; blocked dependencies also
prevent a completed result.

### Infrastructure retry

Transient provider failures may receive at most two retries by default, configurable
from zero to three, with exponential backoff. Retry scheduling, attempt counts and
outcomes remain visible and do not imply medical-quality improvement.

### Length recovery

An unusable `finish_reason=length` response can receive one same-input continuation path. Normal stopped responses are not expanded.

### Protocol recovery

When a worker returns non-empty content with `finish_reason=stop` but malformed structured output, the runtime may make one serialization-only repair call. The call cannot use tools, rerun planning, or add new clinical analysis. Its result must pass the original strict parser.

## What the result does not prove

Completion does not imply medical correctness, and 60/60 is not a production reliability guarantee. It also does not measure the probability of future Provider outages. Clinical quality is evaluated separately, while deployment would require independent safety review, held-out validation, monitoring, and human oversight.
