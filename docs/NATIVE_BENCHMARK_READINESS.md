# Native Benchmark Readiness

`Native implementation ready for COMPOSITE-20 rerun: YES`

This is a readiness decision, not a benchmark result. No benchmark data or answers are
bundled, and no benchmark or judge was run during this integration closure.

## Required integration gates

- **Memory connected:** bounded session history is present in actual Planner and Worker
  requests; current request data is explicitly authoritative; isolation, empty-memory,
  and explicitly disabled-memory behavior are tested.
- **Skills connected:** diagnosis, consultation, research, and synthesis public specs are
  present in their actual model system messages; names and digests are recorded.
- **RAG configurable:** `off`, `fake`, and `milvus` are selected by the runtime factory;
  Milvus configuration/dependency failures are explicit; configured collection names flow
  through the Tool -> Retrieval trace chain.
- **Full Trace connected:** actual Planner/Worker/Synthesis requests and responses are
  recorded, including schemas, usage, finish reason, latency, and errors when available.
- **Redaction connected:** a single trace-write boundary removes credential-shaped values
  while preserving ordinary clinical text.
- **Public tests green:** `tests/test_runtime_claims.py` and the full non-integration suite
  pass; Ruff, CLI, API, replay, and frontend build checks pass.
- **No private dependency:** public production code has no import, runtime path, subprocess
  bridge, or configuration dependency on the private runtime.
- **No private textual carryover:** the existing public provenance scanner remains clean
  after these wiring changes.

Real LLM and real Milvus checks are intentionally `NOT_RUN`: they require operator-owned
credentials, services, models, and a lawfully obtained corpus. License and ownership
confirmation remain separate release-governance blockers, not native runtime wiring gaps.
