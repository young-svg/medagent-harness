# Design Decisions

- Native is the only executable runtime mode; replay is a read-only command.
- Legacy malformed plans have a small deterministic fallback. Invalid declared
  dependency plans fail explicitly instead of dropping edges.
- Independent tasks stay parallel. Dependency graphs are validated and task IDs
  preserved through contract policy; same-role tasks with dependencies are not merged.
- Handoff is a source-linked public per-item result, not a transfer of hidden reasoning,
  confirmed patient facts, or Session Memory. Failed/invalid upstream work blocks descendants.
- Tool schemas are filtered before model calls and checked again at execution.
- Tool-call budget is two per Worker per run by default, shared across its subtasks.
  Argument validation uses the published schema; runtime subtask context is separate.
- Full retrieval bundles stay observable; workers see only compact admitted items.
- Session memory is local and bounded by count and estimated history cost (default
  4096; 0 excludes history; None is unlimited). The byte proxy is not real token usage.
  CLI/FastAPI default Memory follows RuntimeConfig; explicit custom Memory injection
  takes precedence over its budget.
  Long-term memory is excluded.
- Worker coverage is not final coverage. Structured synthesis can be deterministically
  recovered, then final required IDs/content are rechecked after sanitizer and stable edits.
  Guardrail `check.passed` is not a direct completion gate.
- Causal parents are explicit; absent parents stay null. Original audit payloads may
  retain sensitive text/reasoning; filtered Handoff Trace is a different boundary.
- Guardrails distinguish recommendations, history, questions, conditions, and
  same-target contradictions before authorizing a whole-unit edit.
- License selection remains a human decision.
