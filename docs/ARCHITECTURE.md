# Architecture

## System objective

MedAgent Harness is a centralized planner-worker runtime for traceable clinical decision-support workflows. Its design goal is not autonomous diagnosis. It is reliable decomposition, bounded execution, explicit coverage, and reviewable output.

```text
User query
  -> Request understanding
  -> Complexity routing
  -> Specialized worker execution
  -> Tools and optional retrieval
  -> Coverage and contract verification
  -> Final response and observable trace
```

## 1. Request understanding

Clinical prompts frequently combine several explicit questions. `RequestSpec` extracts source-backed request items and preserves their order. If a request cannot be safely decomposed, the complete question remains one required item rather than being guessed or discarded.

`AnswerContract` is a separate semantic layer. It identifies clinical deliverables such as diagnosis, differential diagnosis, investigation, treatment, and follow-up. Keeping the two layers separate prevents a recognized clinical category from hiding an unrecognized but explicit user request.

The completion gate requires both:

```text
UserRequestComplete = every required RequestSpec item has a valid answer
ContractComplete    = every required clinical deliverable is covered
```

## 2. Complexity routing

The planner creates bounded subtasks with explicit request-item and deliverable ownership. The router then selects:

- `single` when one specialist can answer the dependent work coherently;
- `multi` when diagnostic, consultation, or evidence work can be separated productively.

Routing is centralized. Workers do not claim arbitrary work, and request count does not automatically imply worker count.

## 3. Multi-agent collaboration

The public worker roles are:

- **Diagnostic Agent** — clinical pattern assessment, differential diagnosis, and diagnostic workup;
- **Consultation Agent** — treatment, risk management, follow-up, and longitudinal planning;
- **Research Agent** — explicitly requested external evidence and source-aware synthesis.

Each worker receives only its assigned request items, the authoritative current context, the patient-fact ledger, and admitted evidence. Multi-worker drafts are synthesized only after worker execution. A successful assignment does not count as coverage unless the worker returns a valid non-empty answer for that item.

## 4. Tool execution and retrieval

Tool schemas are filtered before each worker model call and revalidated at execution. A configurable tool-call budget prevents unbounded loops.

Retrieval is gated by the request and configuration:

- `off` fails retrieval explicitly;
- `fake` supports deterministic tests and synthetic demos;
- `milvus` connects to a user-supplied, lawfully governed corpus.

Raw retrieval candidates remain outside the worker prompt. Only admitted compact evidence is exposed to the worker, while retrieval decisions remain observable in the trace.

## 5. Verification

Worker output is parsed into request-item answers. Unknown IDs, duplicates, empty answers, and unassigned answers cannot claim coverage. After synthesis, verification checks both RequestSpec coverage and the AnswerContract.

The presentation layer derives patient, clinical, and developer views from the same final answer. It does not create a second medical conclusion.

## 6. Recovery

The runtime separates three failure classes:

- infrastructure retry for bounded transient provider failures;
- length recovery for an unusable truncated generation;
- protocol recovery for non-empty worker content that fails the structured-output schema.

Protocol recovery is deliberately narrow: at most one serialization-only call, temperature zero, no tools, no planner rerun, no tool replay, and no quality-triggered retry. The repaired result must pass the original parser and coverage gates.

## Observability and privacy boundary

Traces record observable messages, tool schemas, tool events, model settings, usage, latency, and errors. Central redaction removes credential-shaped values. Traces are local runtime outputs and are excluded from the public repository.

The public repository includes no patient dataset, private medical corpus, vector database, credentials, model weights, or benchmark answer dump.
