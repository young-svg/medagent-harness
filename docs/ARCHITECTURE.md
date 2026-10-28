# Architecture

## System Objective

MedAgent Harness is a centralized planner-worker runtime for traceable clinical decision-support workflows. Its objective is not autonomous diagnosis or a new foundation model. It turns a compound request into explicit work, runs that work through bounded agent and tool loops, verifies completion, and records the observable execution lifecycle.

## Execution Model

```mermaid
flowchart TB
    subgraph PIPELINE["AGENT HARNESS EXECUTION"]
        direction LR

        subgraph CONTEXT_LAYER["CONTEXT MANAGEMENT"]
            direction TB
            REQUEST(["User Request"])
            CONTEXT["RequestSpec<br/>Stage-aware Context"]
            MEMORY["Session Memory<br/>Bounded · Session-isolated<br/>Process-local"]

            REQUEST --> CONTEXT
            MEMORY --> CONTEXT
        end

        HARNESS[["AGENT HARNESS RUNTIME<br/>CORE CONTROL PLANE<br/><br/>Planner · Complexity Routing<br/>State Management · Agent Dispatch"]]

        subgraph EXECUTION_LAYER["AGENTS & CAPABILITIES"]
            direction TB
            AGENTS["Specialized Agents<br/>Diagnostic · Consultation"]
            CAPABILITIES["Tools & RAG<br/>Tool Policy · Optional Retrieval<br/>Evidence Injection"]

            AGENTS --> CAPABILITIES
        end

        OUTPUT(["Output<br/>Final Answer · Presentation Layer"])

        CONTEXT_LAYER --> HARNESS --> EXECUTION_LAYER --> OUTPUT
    end

    subgraph ASSURANCE["VERIFICATION, RECOVERY & OBSERVABILITY · CROSS-CUTTING"]
        direction LR
        VERIFY["Answer Contract<br/>Completion Gate"] ~~~ RECOVERY["Infrastructure Retry<br/>Protocol Recovery"] ~~~ OBS["Trace · Usage · Latency<br/>Benchmark"]
    end

    PIPELINE -.->|control signals and outcomes| ASSURANCE

    classDef core fill:#2563eb,stroke:#1d4ed8,stroke-width:4px,color:#ffffff,font-weight:700
    class HARNESS core
    style PIPELINE fill:#ffffff,stroke:#cbd5e1,stroke-width:1px
    style CONTEXT_LAYER fill:#f8fafc,stroke:#94a3b8,stroke-width:1px
    style EXECUTION_LAYER fill:#f8fafc,stroke:#94a3b8,stroke-width:1px
    style ASSURANCE fill:#f8fafc,stroke:#94a3b8,stroke-width:1px
```

For a single successful worker, the harness can use the worker answer directly instead of running multi-worker synthesis. Both paths still pass through the guardrail and presentation stages.

## 1. Request Structuring and Context Engineering

- **Problem:** A clinical prompt can contain several explicit questions, while a broad intent label can silently hide one of them.
- **Design:** `RequestSpec` preserves ordered user-request items. `AnswerContract` separately identifies requested clinical deliverables such as diagnosis, investigation, treatment, and follow-up. An evidence ledger represents patient facts without inventing missing data.
- **Runtime behavior:** The current case and question are authoritative. The Planner receives task-level context; each worker receives its assigned request IDs, role-specific subtask, bounded session context, contract, profiles, and patient-fact ledger. Tool or retrieval evidence enters only through the worker loop.
- **Failure handling:** If safe request decomposition is unavailable, the full question remains a required item. Unknown, duplicate, unassigned, or empty request-item answers cannot claim coverage.

This is stage-aware context construction, not one shared prompt and not a claim of complete long-context management.

## 2. Complexity Profiling, Planning, and Routing

- **Problem:** One worker is sufficient for focused dependent work, while broad cross-role requests benefit from explicit decomposition.
- **Design:** A deterministic complexity profile captures breadth, requested deliverables, cross-role needs, and explicit external-evidence requirements. The centralized Planner assigns bounded subtasks and ownership to the Diagnostic, Consultation, or Research worker. The Router reports a `single` or `multi` route from the validated plan.
- **Runtime behavior:** Every required request item must map to a subtask. Capability policy exposes retrieval-oriented tools only when the request explicitly requires external evidence. Workers do not create or claim arbitrary work.
- **Failure handling:** Invalid planner structure falls back to a dispatchable bounded subtask with a valid worker. Contract policy normalizes ownership, and the Router has a diagnostic dispatchability fallback if no valid worker survives validation.

## 3. Agent Harness and Multi-Agent Orchestration

- **Problem:** Independent model calls need common control over state, ownership, timeouts, tools, completion, and traceability.
- **Design:** `NativeMedAgentEngine` coordinates the lifecycle; `AgentLoop` owns each worker's model/tool loop. Worker roles have explicit scopes and safety boundaries. The harness, rather than the model, owns dispatch and completion state.
- **Runtime behavior:** Independent subtasks run concurrently. Validated `depends_on`
  edges wait for successful upstream results; execution timeouts begin after waiting.
  Sanitized, structured Handoff carries source task/Worker IDs, per-item answers and
  the source Worker event ID, separately from patient facts and Memory. Results retain
  plan order; same-role dependency tasks keep their identities. Contract reconciliation
  preserves required deliverable ownership without dropping dependencies. The Diagnostic
  worker covers assessment/workup, Consultation covers care, and Research covers evidence.
- **Failure handling:** Worker exceptions and timeouts become explicit failed-worker results.
  Invalid dependency graphs fail explicitly. Unusable upstream output blocks descendants
  without their model calls, even if unrelated work covers the same request. Handoff filters
  complete and unterminated reasoning tags before its Trace event and downstream prompt.

## 4. Tool Use and Optional RAG

- **Problem:** Unbounded or globally exposed tools make agent behavior difficult to control and audit.
- **Design:** The tool registry filters schemas by worker role and request-derived capability policy, revalidates tool names at execution, and enforces a per-worker call budget. Retrieval is a tool-backed optional capability with `off`, deterministic `fake`, and user-configured `milvus` modes.
- **Runtime behavior:** Workers can call visible functions inside the AgentLoop. Retrieval builds and routes a query, records raw candidates, applies score/count admission, deduplicates evidence, and returns only compact admitted evidence to the worker.
- **Failure handling:** Invisible, missing, malformed, or over-budget tool calls return explicit errors to the loop. `off` mode fails retrieval explicitly; incomplete Milvus configuration or missing optional packages raises a configuration error instead of silently using private local data.

The base runtime does not require RAG, and retrieval is not claimed as the primary source of the reported benchmark improvement. Users must supply and govern any real corpus themselves.

## 5. Session Memory

- **Problem:** Follow-up requests need relevant prior turns without mixing users or treating stale context as authoritative.
- **Design:** `SessionMemory` is bounded, process-local, and keyed by `session_id`. It is session-scoped state, not persistent cross-session storage.
- **Runtime behavior:** One shared history snapshot is injected into Planner and all Workers.
  Count limiting precedes whole-message, oldest-first trimming to an estimated budget
  (default 4096; 0 excludes history; None disables the budget). UTF-8 JSON bytes plus
  framing are a conservative proxy, not actual tokenizer usage or a model-window hard
  limit. Current input/task context is not trimmed. CLI/API default Memory inherits
  RuntimeConfig; explicit custom Memory retains its settings. Accepted, deduplicated
  writes are counted in Trace. Memory does not persist patient records.
- **Failure handling:** Memory can be disabled, cleared per session, and never crosses session IDs. The recent-message limit prevents unbounded accumulation; process restart intentionally clears the state.

## 6. Worker Responses and Completion Gates

- **Problem:** A worker can return fluent text while omitting an assigned item or violating the structured response protocol.
- **Design:** Worker output maps answers to assigned `request_item_id` values. The runtime computes two independent gates: required `RequestSpec` coverage and required `AnswerContract` coverage.
- **Runtime behavior:** Only schema-valid, non-empty answers from successful workers count. Multiple drafts are synthesized only after both coverage gates are complete.
- **Failure handling:** Unknown IDs, duplicate IDs, empty answers, and unassigned answers do not count. Missing required request items or deliverables returns an explicit `incomplete` result with the missing IDs instead of presenting a partial answer as complete.

## 7. Synthesis, Guardrails, and Presentation

- **Problem:** Combining worker drafts can introduce omissions or inconsistencies, and presentation should not create a second clinical conclusion.
- **Design:** The Synthesizer receives the contract, request map, profiles, and successful worker drafts. A conservative Guardrail checks contract coverage and contradictions against the patient-fact ledger, then applies only stable whole-unit edits. Presentation derives patient, clinical, and developer views from the same final answer.
- **Runtime behavior:** Multi-worker drafts are reduced to the smallest complete response; a single-worker draft bypasses synthesis. The guardrail runs on either path. Provider-backed presentation transformation is optional.
- **Failure handling:** Provider failure or exhausted synthesis is a stage error. Malformed,
  duplicate, missing or empty per-item synthesized answers can be recovered deterministically
  from corresponding validated Worker answers, without a repair call. Final required IDs and
  substantive content are rechecked after sanitizer and stable Guardrail edits. Unrecoverable
  answers return incomplete; Worker coverage and final coverage remain separate. Incomplete
  runs skip the optional presentation transform. If the transform fails, deterministic
  presentation remains available without changing the final answer or coverage state.

## 8. Reliability Control

- **Problem:** Transient provider faults, truncated generations, and malformed structured output require different responses. Treating all three as a generic retry can replay work or alter semantics.
- **Design:** Recovery is stage- and failure-specific, bounded, and observable.
- **Runtime behavior:**
  1. **Infrastructure retry:** a worker retries at most twice for classified transient transport, rate-limit, or server failures, with one- and two-second backoff by default. Attempt count, delay, and error type remain visible in the trace.
  2. **Length completion recovery:** a stage can make at most one bounded continuation attempt after an unusable length-truncated response.
  3. **Worker protocol recovery:** after non-empty semantic content with `finish_reason=stop` fails parsing, one temperature-zero call receives only the malformed output, allowed request IDs, and required schema.
- **Failure handling:** Protocol recovery has no tools, no Planner rerun, no tool replay, no full case or session reinjection, and no quality-triggered retry. Repaired output must pass the strict parser and the same downstream completion gates; a failed repair terminates that worker path.

## 9. Observability and Evaluation

- **Problem:** Agent quality and reliability cannot be assessed from the final answer alone.
- **Design:** A central trace recorder emits structured JSONL events and applies credential-shaped redaction at the write boundary. Evaluation remains separate from runtime execution.
- **Runtime behavior:** Trace records context, model/tool/retrieval boundaries, coverage,
  recovery, Handoff provenance and final status. Causal parents are explicit; unrelated
  concurrent events are not connected by append order. Worker request coverage and final
  output coverage are distinct, and Replay uses final coverage when available. Raw audit
  payloads may contain reasoning and sensitive text; credential redaction is not privacy
  certification. Do not publish raw local traces or expose them to untrusted users.
- **Failure handling:** Errors and recovery outcomes are events with sanitized error metadata. Local traces are excluded from the public repository and can be summarized or replayed read-only.

Evaluation evidence is documented in:

- [Benchmark](BENCHMARK.md)
- [Reliability](RELIABILITY.md)
- [Failure analysis](FAILURE_ANALYSIS.md)

## Capability Boundaries

Implemented capabilities include context engineering, centralized planning, multi-agent orchestration, controlled function calling, optional evidence retrieval, session-scoped memory, completion verification, bounded recovery, tracing, and offline evaluation fixtures.

The project does not claim persistent cross-session memory, autonomous learning, skill self-evolution, a complete reflection framework, complete long-context management, clinical validation, or medical-device approval.
