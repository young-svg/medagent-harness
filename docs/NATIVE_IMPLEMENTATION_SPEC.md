# Native Implementation Behavior Specification

This document specifies observable behavior and public interfaces. It does not
describe or reproduce any predecessor function body or prompt.

Current behavior is the local main implementation, including A1-C1. Historical
evaluation reports are not current runtime guarantees. See [architecture](architecture.md)
for a runnable synthetic dependency example and [README](../README.md) for configuration.

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

- Input: question, RequestSpec, contract, ledger, complexity, and trimmed history.
- Output: structured subtasks with request/deliverable IDs and `depends_on=[]` by default.
  Router labels single/multi; NativeMedAgentEngine schedules the validated graph.
- State: observable planner request/response events.
- Failure: legacy malformed output permits a deterministic fallback; missing/invalid
  agents are defaulted during parsing. Invalid declared dependencies (unknown IDs,
  self-references, cycles, duplicate IDs/edges) raise `PlanDependencyError`. Contract
  policy preserves dependency identities without folding/merging; removing graph tasks
  fails explicitly. It never silently removes dependencies to permit parallel execution.
- Interface: `Planner.plan`, `Planner.parse`, `Router.route`.

## 4. Worker and Agent Loop

- Input: role, subtask, RequestSpec, contract, ledger, common trimmed history,
  optional sanitized upstream handoffs, and compact admitted tool evidence.
- Output: `WorkerResult` with public per-item answers and a runtime-only source event ID.
- State: model messages and per-worker tool-call count.
- Scheduling: roots run concurrently; a dependent task waits for all direct upstream
  results. Its timeout starts when Worker execution starts, not when dependency waiting
  begins. Results are aggregated in plan order.
- Failure: failed/timed-out/malformed/empty upstream answers block downstream LLM calls.
  Blocked runs return incomplete, even if overlapping work satisfies Worker coverage.
  All-independent, all-worker failure retains the existing explicit engine error.
- Handoff: contains source task ID, Worker, sanitized RequestItemAnswers, success status,
  and source `worker_draft` event ID. Required upstream IDs, duplicates and substantive
  content are checked. It is not merged into patient facts or Memory.
- Interface: `AgentLoop.execute`.

## 5. Tool Permission and Budget

- Input: agent identity, requested tool, arguments.
- Output: tool result or explicit denial/error.
- State: at most two calls per worker by default.
- Failure: hidden, unregistered, malformed, or over-budget calls do not execute handlers.
  Checks run in permission/registration/budget/argument order. Invalid arguments consume
  an attempt, as do handler errors; earlier denials do not. Errors become tool results.
- Validation: matches public object/string schemas, including `additionalProperties`;
  string emptiness/length restrictions apply only if declared. Runtime task context is
  keyword-only call metadata, not a model argument or a schema bypass.
- Interface: `ToolRegistry`.

## 6. Retrieval-Augmented Generation

- Input: original question context, immutable per-call Subtask ID/description,
  all assigned deliverable IDs and RequestItems, and tool input once. Explicit
  unassigned source spans are excluded; unsplit questions are kept once.
- Output: deterministic query, collection choice, full `EvidenceBundle`, and compact
  admitted evidence.
- State: full bundle stays in trace/presentation metadata; workers receive admitted
  compact items only.
- Failure: an empty backend produces an empty bundle without fabricated evidence.
- Interface: `RetrievalBackend`, `FakeRetrievalBackend`, `MilvusRetrievalBackend`.
- Boundary: fake mode is empty unless items are injected. Real Milvus needs operator-owned
  models/corpus/services. Named clinical retrieval tools wrap this backend; symptom/risk
  utilities only return input/notices. Score admission is not clinical validation.

## 7. Synthesis

- Input: successful Worker per-item drafts, RequestSpec and contract.
- Output: JSON `answers` keyed by `request_item_id`, subsequently validated and rendered
  in RequestSpec order. One successful Worker bypasses synthesis.
- State: synthesis input/output trace events.
- Failure: synthesis provider failure terminates explicitly. Invalid JSON/structure,
  duplicate/invalid IDs, empty headings, and omissions can trigger deterministic recovery
  using corresponding validated Worker answers, without an additional repair LLM call.
  Sanitization/stable edits precede final coverage; unrecoverable content yields incomplete.
- Interface: native engine synthesis stage.

## 8. Session Memory

- Input: session ID, recent messages, and current input.
- Output: bounded context with current input last and authoritative.
- State: isolated per-session list with trimming and near-term exact deduplication.
- Budget: default 4096, 0 excludes history, None is unlimited. Apply the count limit
  first (reserve current-input slot), then remove oldest whole history messages until
  estimated cost fits. `recent_limit=1` injects no history. Stored text is not truncated.
- Estimate: UTF-8 bytes of serialized history JSON plus eight per message, zero for
  empty history. This includes roles/escaping but is not actual tokenizer usage or a
  provider context-window guarantee; current input/task context are never trimmed.
- Consumers: one read snapshot is reused by Planner/all Workers. CLI and FastAPI
  default Memory inherit RuntimeConfig's budget, including environment overrides.
  FastAPI retains one Coordinator and session-isolated Memory across requests;
  explicitly injected Memory keeps its own settings.
- Failure: blank, duplicate and disabled writes return False; accepted writes return
  True. Memory Write Trace counts accepted insertions, not attempts or net list growth.
  Disabled Memory neither reads nor writes, but does not clear previous history.
- Trace: reports effective budget, heuristic name/use, retained counts, and count/budget
  removals. No mutable global last-read metadata is used.
- Interface: `SessionMemory`.

## 9. Guardrail

- Input: final draft, contract, and patient ledger.
- Output: detected issues plus optional safe edits.
- State: none.
- Failure: ambiguous or mixed units are reported/preserved rather than destructively changed.
- Interface: `Guardrail.check`.
- Boundary: this deterministic medical-consistency checker is not a medical device or
  semantic proof of correctness. Its `passed` flag alone does not decide completed.

## 10. Stable Editing

- Input: `StableDraft` and DELETE, REPLACE, or DOWNGRADE edits.
- Output: deterministically rendered answer and application status.
- State: whole paragraph/group units retain stable IDs.
- Failure: missing or mixed units reject the edit set.
- Interface: `StableUnit`, `StableDraft`, `apply_stable_edits`.
- Final safety/coverage: `sanitize_answer` removes complete/nested/unterminated
  `think`/`analysis` blocks including their contents. Whole-unit edits retain RequestItem
  ownership; final required coverage is checked again after edits and sanitization.

## 11. Trace and Replay

- Input: observable runtime events.
- Output: JSONL events with event ID, parent ID, run ID, timestamp, stage, type,
  agent, and payload; replay reads only this schema.
- State: append-only per-run trace.
- Causality: only explicit known parent IDs are attached; unknown parents stay null.
  Dependency waits/readiness/blocks and handoffs include task IDs; Handoff source event
  links intentionally cross Worker boundaries. JSONL and Replay retain their interface.
- Privacy: raw LLM requests/responses, Worker drafts, synthesis and other audit events
  may retain sensitive text and reasoning. Credential-pattern redaction is not patient
  de-identification. Sanitized Handoff events do not make the entire Trace safe to publish.
- Failure: a missing trace raises `FileNotFoundError`; execution errors receive an
  error event and terminal run event.
- Interface: `TraceRecorder`, `read_trace`, `replay`.

## 12. Failure Handling

- Input: configuration, model, worker, tool, retrieval, and checker exceptions.
- Output: explicit error or bounded fallback where this specification permits one.
- State: failures are visible in trace; no silent alternate runtime is selected.
- Failure: all-worker failure prevents a successful answer.
- Interface: `EngineExecutionError`, `RuntimeConfigurationError`.
