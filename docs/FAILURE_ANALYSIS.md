# Failure Analysis

## Initial Issue

An early 20-case benchmark produced 2 incomplete cases.

## Root Cause

The worker generated clinically relevant content but malformed the required structured response:

- invalid JSON;
- parser rejection of the response.

## Diagnosis

The observed failure was not attributed to:

- medical reasoning failure;
- planner failure;
- network failure.

It was a protocol-boundary failure between successful text generation and strict worker-response parsing.

## Solution

The runtime added bounded protocol recovery with these constraints:

- maximum one recovery attempt per worker;
- no planner rerun;
- no tool replay;
- no quality retry;
- original parser, request-coverage gate, and answer-contract gate preserved;
- recovery receives no tool definitions or permissions;
- recovery is limited to re-serializing the existing worker response.

Malformed, empty, wrong-ID, tool-calling, or schema-invalid recovery output still fails closed.

## Result

The final fixed 60-case valid-execution set completed 60/60 cases. Protocol recovery was validated, malformed worker outputs fell to zero, and tool replay remained zero.

Provider attempts that return no content solely because of a classified transient infrastructure exception are treated as invalid attempts rather than Agent-logic outcomes. They may be repeated only with the identical input and configuration. The runtime allows at most two infrastructure retries with one- and two-second backoff, and offline fault injection verified recovery after two consecutive `ConnectError` events.

Valid responses are not regenerated for quality, and the 60/60 result is not evidence of perfect reliability or general medical correctness.
