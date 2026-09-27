# Design

## Design principles

### Preserve the user's actual request

Clinical capability labels are useful but incomplete. The runtime therefore preserves source-backed request items independently of its clinical taxonomy. Unknown request types remain required instead of being coerced into a nearby label.

### Use multiple agents only when work is separable

Multi-agent execution adds coordination cost. The router selects it only when separate diagnostic, consultation, or research work can improve coverage. Closely coupled tasks stay with one worker.

### Make completion a verified property

Plans and assignments are intentions, not evidence of completion. Only schema-valid, non-empty answers can satisfy a request item or deliverable.

### Keep side effects bounded

Tools are allowlisted by role, validated at execution, and limited by budget. A protocol-recovery call has no tools, so a serialization failure cannot replay a prior side effect.

### Fail explicitly at external boundaries

Real retrieval requires explicit configuration and dependencies. Missing configuration does not silently fall back to fake evidence. The local deterministic model and fake retrieval exist for tests and demonstrations, not as hidden production substitutes.

### Keep one medical answer

Presentation views share the native final answer. Plain-language and developer metadata improve accessibility and observability without creating conflicting clinical conclusions.

## Important invariants

- Current request data overrides session history.
- Session memory is bounded and process-local.
- Planner and worker ownership is explicit.
- Retrieval evidence is admitted before prompt injection.
- A tool executes only after schema and budget checks.
- Recovery never bypasses the original parser or completion gate.
- Observable traces do not expose hidden chain-of-thought.

## Deliberate non-goals

- replacing clinicians or issuing autonomous medical diagnoses;
- shipping a clinical corpus, patient dataset, or vector database;
- training or distributing model weights;
- claiming general medical superiority from a fixed development benchmark;
- rearranging stable modules solely for cosmetic directory symmetry.

## Package boundaries

The established package layout is retained because it reflects runtime boundaries and avoids import churn:

- `medagent/context` — RequestSpec, AnswerContract, and evidence ledger;
- `medagent/planning` — planner, complexity assessment, and router;
- `medagent/agents` — specialized worker definitions;
- `medagent/runtime` — orchestration and recovery;
- `medagent/tools` — tool registry and schemas;
- `medagent/retrieval` — backend, routing, admission, and evidence;
- `medagent/guardrails` — contract and consistency checks;
- `medagent/presentation` — derived response views;
- `medagent/observability` — trace and replay support.

Reusable benchmark scoring remains in top-level `eval/`; moving it into the package during release cleanup would create unnecessary import and packaging risk.
