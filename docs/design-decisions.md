# Design Decisions

- Native is the only executable runtime mode; replay is a read-only command.
- Planner parsing is strict, with a small deterministic fallback.
- Tool schemas are filtered before model calls and checked again at execution.
- Tool-call budget is two per worker by default.
- Full retrieval bundles stay observable; workers see only compact admitted items.
- Session memory is local and bounded. Long-term memory is excluded.
- Guardrails distinguish recommendations, history, questions, conditions, and
  same-target contradictions before authorizing a whole-unit edit.
- License selection remains a human decision.
