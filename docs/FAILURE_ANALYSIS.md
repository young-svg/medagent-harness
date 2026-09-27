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

The fixed 60-case reliability benchmark completed 59/60 cases. Protocol recovery was validated, and tool replay remained zero.

The result demonstrates that the recovery path is bounded and useful; it does not establish perfect reliability or general medical correctness.
