# Native Implementation Behavior Specification

This document specifies observable behavior and public interfaces. It does not
describe or reproduce any predecessor function body or prompt.

## 1. Context and Answer Contract

- Input: case description and user question.
- Output: validated `AnswerContract` with one or more supported deliverables.
- State: no mutation; deterministic classification with a comprehensive fallback.
- Failure: invalid contracts fail before dispatch.
- Interface: `build_answer_contract`, `AnswerContract`.

## 2. Evidence Ledger

- Input: the current case description.
- Output: stable patient-fact `Finding` records and explicit source conflicts.
- State: none; current input is authoritative for the run.
- Failure: duplicate identifiers or empty findings fail validation.
- Interface: `build_evidence_ledger`, `EvidenceLedger`, `Finding`.

## 3. Planner and Router

- Input: question, contract, ledger.
- Output: structured dispatchable subtasks, assigned agents, and single/multi route.
- State: observable planner request/response events.
- Failure: malformed JSON, a missing agent, or an invalid agent uses a deterministic
  dispatchable fallback.
- Interface: `Planner.plan`, `Planner.parse`, `Router.route`.

## 4. Worker and Agent Loop

- Input: role definition, subtask, contract, ledger, admitted compact evidence.
- Output: `WorkerResult` draft.
- State: model messages and per-worker tool-call count.
- Failure: timeout or worker failure is recorded; the run may continue if another
  worker completed.
- Interface: `AgentLoop.execute`.

## 5. Tool Permission and Budget

- Input: agent identity, requested tool, arguments.
- Output: tool result or explicit denial/error.
- State: at most two calls per worker by default.
- Failure: hidden, unregistered, malformed, or over-budget calls do not execute.
- Interface: `ToolRegistry`.

## 6. Retrieval-Augmented Generation

- Input: task context and tool input.
- Output: deterministic query, collection choice, full `EvidenceBundle`, and compact
  admitted evidence.
- State: full bundle stays in trace/presentation metadata; workers receive admitted
  compact items only.
- Failure: an empty backend produces an empty bundle without fabricated evidence.
- Interface: `RetrievalBackend`, `FakeRetrievalBackend`, `MilvusRetrievalBackend`.

## 7. Synthesis

- Input: successful worker drafts and requested deliverables.
- Output: one final draft without duplicated worker text or hidden reasoning.
- State: synthesis input/output trace events.
- Failure: model failure terminates the run explicitly.
- Interface: native engine synthesis stage.

## 8. Session Memory

- Input: session ID, recent messages, and current input.
- Output: bounded context with current input last and authoritative.
- State: isolated per-session list with trimming and near-term exact deduplication.
- Failure: blank writes are ignored.
- Interface: `SessionMemory`.

## 9. Guardrail

- Input: final draft, contract, and patient ledger.
- Output: detected issues plus optional safe edits.
- State: none.
- Failure: ambiguous or mixed units are reported/preserved rather than destructively changed.
- Interface: `Guardrail.check`.

## 10. Stable Editing

- Input: `StableDraft` and DELETE, REPLACE, or DOWNGRADE edits.
- Output: deterministically rendered answer and application status.
- State: whole paragraph/group units retain stable IDs.
- Failure: missing or mixed units reject the edit set.
- Interface: `StableUnit`, `StableDraft`, `apply_stable_edits`.

## 11. Trace and Replay

- Input: observable runtime events.
- Output: JSONL events with event ID, parent ID, run ID, timestamp, stage, type,
  agent, and payload; replay reads only this schema.
- State: append-only per-run trace.
- Failure: a missing trace raises `FileNotFoundError`; execution errors receive an
  error event and terminal run event.
- Interface: `TraceRecorder`, `read_trace`, `replay`.

## 12. Failure Handling

- Input: configuration, model, worker, tool, retrieval, and checker exceptions.
- Output: explicit error or bounded fallback where this specification permits one.
- State: failures are visible in trace; no silent alternate runtime is selected.
- Failure: all-worker failure prevents a successful answer.
- Interface: `EngineExecutionError`, `RuntimeConfigurationError`.
