# Architecture

## End-to-end call flow

```mermaid
sequenceDiagram
  participant U as User/API
  participant C as Coordinator
  participant M as Session Memory
  participant P as Contract/Ledger + Planner
  participant W as Worker(s)
  participant R as Retrieval/Tools
  participant G as Guardrail
  participant T as Trace
  U->>C: case, question, session ID
  C->>T: run_start/context
  C->>M: recent context + current input
  C->>P: contract, ledger, plan, route
  P->>T: public decisions
  C->>R: deterministic query + collection
  R-->>W: compact admitted evidence only
  C->>W: one or more dispatchable subtasks
  W-->>C: drafts
  C->>G: synthesized answer
  G-->>C: detection, conservative patch, sanitized answer
  C->>M: commit user + final output
  C->>T: final_answer/run_end
  C-->>U: presentation + trace summary
```

## Module contracts

| Module | Input | Output | State | Failure behavior |
|---|---|---|---|---|
| Context | question/case | AnswerContract, EvidenceLedger | none | validation rejects malformed structures |
| Session memory | session ID/messages | trimmed recent context | in-process, per session | exact adjacent duplicates ignored |
| Planner | contract/question | dispatchable Plan | none | deterministic valid fallback |
| Router | Plan | single/multi Route | none | diagnostic single-worker fallback |
| Tool runtime | worker/tool/arguments | handler result | registered handlers | visibility checked again at execution |
| RAG | task context/tool intent | EvidenceBundle | backend-owned | empty bundle; no fabricated evidence |
| Workers | subtask/compact evidence | WorkerResult | none | failure isolated; successful drafts retained |
| Synthesis | successful drafts | professional answer | none | deterministic concatenation fallback |
| Guardrail | answer/contract | findings + optional exact patch | none | ambiguous edit preserves original |
| Trace | public execution events | JSONL + summary | run directory | error event then closed failed lifecycle |
| Presentation | final artifacts | three-view response | none | empty safe fields rather than invented facts |

Single-agent mode avoids unnecessary synthesis overhead. Multi-agent mode runs
independent worker calls concurrently, while planning and final synthesis remain
centralized. Procedural skills are explicitly loaded files; tool schemas are
capability interfaces and are not skills.

The trace may contain case text and must be treated as sensitive in real
deployments. Run directories are ignored by Git. It never records API keys or
private chain-of-thought.

