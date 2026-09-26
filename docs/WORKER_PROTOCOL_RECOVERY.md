# Worker Protocol Recovery

## Problem

The final COMPOSITE-20 audit identified two Worker failures where the provider request
succeeded, `finish_reason` was `stop`, content was non-empty, and the clinical answer was
present, but the WorkerResponse JSON protocol was malformed:

- MED-057 emitted a malformed first object, self-correction prose, and a second JSON object.
- MED-058 emitted a final post-tool response with a missing string-closing quote while the
  assigned treatment answer remained present later in the response.

Both responses were correctly rejected by the conservative parser. The runtime previously
had infrastructure retry, length recovery, and conservative trailing-delimiter parsing, but
no bounded recovery for this specific successful-generation/protocol-failure state.

## Trigger

Protocol recovery is implemented only in the Worker `AgentLoop`, after the final response is
returned and before a `WorkerResult` is finalized. It requires all of the following:

- the provider request completed without raising an exception;
- the final response has `finish_reason == "stop"`;
- content is non-empty;
- the response has no tool calls;
- the existing WorkerResponse parser returns no valid assigned answers;
- the Worker has not already used its single protocol-recovery attempt; and
- the generation is not in `length_exhausted` state.

The implementation constant is:

```text
MAX_PROTOCOL_RECOVERY_PER_WORKER = 1
```

Because the mechanism exists only inside `AgentLoop.execute`, it cannot run for Planner,
Synthesis, Checker, or Presentation output.

## Non-trigger cases

Protocol recovery does not run for:

- provider exceptions, including connection errors, timeouts, HTTP 429, and provider 5xx;
- `finish_reason == "length"` or length exhaustion;
- empty content;
- a response that still contains tool calls;
- output already accepted by direct parsing;
- output accepted by the existing conservative trailing-closing-delimiter recovery;
- short, low-quality, or medically weak content that is structurally valid;
- Judge or quality signals.

Infrastructure retry and length recovery retain their existing independent paths and counts.

## Recovery semantics

Recovery is a serialization-only provider call using the same client/model with temperature
forced to `0.0`, no tools, no tool choice, and no response-format shortcut. The request has
exactly two messages:

1. a dedicated serialization-repair system instruction; and
2. a JSON payload containing only:
   - `malformed_worker_response`;
   - `allowed_request_item_ids`; and
   - `required_schema`.

It does not include the original case, patient-fact ledger, memory, evidence bundle, original
Worker messages, tool schemas, or tool permissions. The instruction prohibits new analysis,
new claims, tools, commentary, markdown, and multiple JSON objects.

The returned content is not trusted directly. It must be direct, valid JSON with the exact
WorkerResponse schema and valid assigned request IDs. It then continues through the existing
request-item collection, RequestSpec coverage, AnswerContract coverage, and Completion Gate.
Legacy single-item plain text and a second malformed/recoverable JSON response are rejected.

This is protocol re-serialization, not a medical quality retry.

## Tool side-effect safety

The recovery call is made outside the Worker tool loop and receives `tools=None`. Tool calls
returned despite that constraint are rejected and never executed. A successful tool result
already present in the original Worker loop remains in that loop; it is not copied into the
recovery request and its handler is not invoked again.

The MED-058-like regression test executes `assess_risk` once, produces malformed JSON in the
post-tool response, repairs the protocol, and verifies that the handler count remains exactly
one.

## Boundedness

The attempt count is initialized once per Worker execution, outside all tool-loop generations.
Whether malformed output occurs before or after a tool call, only one protocol recovery can be
made. A malformed, empty, wrong-ID, schema-invalid, length-finished, or tool-calling recovery
response ends in the original Worker failure path; there is no third call and no nested
protocol recovery.

Protocol recovery has separate observability from infrastructure and length mechanisms:

- `worker_protocol_recovery_start` records Worker/subtask, original parse status,
  original finish reason, attempt, and maximum attempts.
- `worker_protocol_recovery_result` records provider outcome, provider error type, recovery
  finish reason, recovery parse status, success, token usage, and latency.
- Worker results expose `protocol_recovery_count` and
  `protocol_recovery_success_count`.
- trace summaries aggregate both counts.

The recovery provider call is also represented by the normal `llm_request`/`llm_response`
events, so global LLM calls, tokens, and latency remain observable. It is not counted as an
infrastructure retry or length recovery.

## Validation

Deterministic fake-provider tests cover:

- valid JSON: no additional call;
- existing trailing-closing-delimiter recovery: no additional call;
- MED-057-like malformed JSON + self-correction + second object: exactly one successful
  recovery with RQ1/RQ2 retained;
- MED-058-like post-tool malformed JSON: exactly one recovery, RQ3 and TREATMENT_PLAN covered,
  and one total tool-handler invocation;
- a second malformed response: failure after one recovery;
- wrong request ID: no assigned coverage;
- empty answer: failure;
- legacy plain-text recovery response: failure;
- non-strict recovery JSON: failure;
- length response: existing length recovery only;
- connection error and timeout: existing infrastructure retry only; and
- recovery response containing tool calls: rejection without tool execution.

Validation commands and final results:

```text
python -m pytest -q -p no:cacheprovider --basetemp <workspace-temp>
172 passed

python -m ruff check medagent tests
All checks passed!

git diff --check
PASS
```

No COMPOSITE-20, MED-057/MED-058 live rerun, 60-case benchmark, Judge, or provider request was
performed during this implementation.

## Remaining limitations

- Deterministic tests prove bounded control flow and validation behavior, not an empirical
  production recovery rate.
- The repair provider can still fail, return an invalid schema, omit an assigned answer, or
  alter wording despite the restrictive instruction. Parser and coverage gates remain the
  enforcement boundary in those cases.
- No heuristic quote/comma repair, last-object selection, or malformed-text acceptance was
  added.
- A 60-case reliability benchmark is still required to measure the mechanism on a frozen
  candidate; no 99% or 100% reliability claim is made here.
