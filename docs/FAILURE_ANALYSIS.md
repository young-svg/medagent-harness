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

The original fixed 60-case reliability run completed 59/60 cases. Protocol recovery was validated, malformed worker outputs fell to zero, and tool replay remained zero. Its remaining incomplete case was a different failure class: `MED-057` received no Provider response content because the initial request and one infrastructure retry both raised `ConnectError`.

The runtime subsequently increased worker infrastructure handling to at most two retries with one- and two-second backoff. Offline fault injection verified recovery after two consecutive `ConnectError` events. A single same-input targeted rerun of `MED-057` then completed on its first Provider attempt with full request and contract coverage.

The reported 60/60 reliability value combines the 59 unchanged original successes with that targeted rerun. The original 59/60 run remains part of the audit trail; the composite is not described as a new single-pass benchmark or as evidence of perfect reliability or general medical correctness.
