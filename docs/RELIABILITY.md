# Reliability

## Reliability objective

The runtime treats completion as a control-flow and coverage property. A provider response is not considered successful merely because it is non-empty.

## Fixed 60-case result

| Metric | Result |
| --- | ---: |
| Cases | 60 |
| Original single-pass completed | 59 |
| Targeted infrastructure rerun | 1 case (`MED-057`) |
| Composite completed | 60 |
| Composite incomplete | 0 |
| Composite non-empty final answers | 60 |
| Tool replay during protocol recovery | 0 |
| Protocol recovery path | Validated |

The original fixed development run is retained at 59/60. `MED-057` was incomplete only because its initial worker request and one bounded infrastructure retry both raised `ConnectError`; no Provider content was returned for protocol parsing or clinical completion.

After increasing the bounded worker infrastructure policy to at most two retries with exponential backoff, only `MED-057` was executed once more with the identical frozen input and model configuration. It completed on the first Provider attempt, with complete request and contract coverage and no protocol recovery. The composite keeps the other 59 original rows unchanged. It is explicitly a targeted-infrastructure-rerun composite, not a new single-pass run or a quality-triggered answer retry.

## Reliability mechanisms

### Request and contract coverage

Each required request item and clinical deliverable must be backed by a valid non-empty worker answer. Planner assignment alone does not count as completion.

### Infrastructure retry

Transient provider failures may receive at most two retries with exponential backoff. Retry scheduling, attempt counts, and outcomes remain visible and do not imply medical-quality improvement.

### Length recovery

An unusable `finish_reason=length` response can receive one same-input continuation path. Normal stopped responses are not expanded.

### Protocol recovery

When a worker returns non-empty content with `finish_reason=stop` but malformed structured output, the runtime may make one serialization-only repair call. The call cannot use tools, rerun planning, or add new clinical analysis. Its result must pass the original strict parser.

## What the result does not prove

Completion does not imply medical correctness, and the 60/60 composite is not a production reliability guarantee. It also does not measure the probability of future Provider outages. Clinical quality is evaluated separately, while deployment would require independent safety review, held-out validation, monitoring, and human oversight.
