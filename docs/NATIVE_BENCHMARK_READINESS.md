# Native Benchmark Readiness

`A1-C1 main: offline engineering checks available; real-provider benchmark NOT_RUN here`

The earlier integration closure marked COMPOSITE-20 readiness YES. That historical decision
is not a current clinical score or recertification after A1-C1 changes. No benchmark data or
answers are bundled. The current main must be identified and rerun explicitly before making
new provider, routing, medical quality, or regression claims.

## Required integration gates

- **Memory connected:** bounded session history is present in actual Planner and Worker
  requests; current request data is explicitly authoritative; isolation, empty-memory,
  and explicitly disabled-memory behavior are tested.
  History now also has a conservative estimated budget; current input is not trimmed.
  Verify the effective `memory_read` budget, especially with explicit Memory injection.
- **Skills connected:** diagnosis, consultation, research, and synthesis public specs are
  present in their actual model system messages; names and digests are recorded.
- **RAG configurable:** `off`, `fake`, and `milvus` are selected by the runtime factory;
  Milvus configuration/dependency failures are explicit; configured collection names flow
  through the Tool -> Retrieval trace chain.
- **Full Trace connected:** actual Planner/Worker/Synthesis requests and responses are
  recorded, including schemas, usage, finish reason, latency, and errors when available.
- **Redaction connected:** a single trace-write boundary removes credential-shaped values
  while preserving ordinary clinical text.
- **Completion/dependencies:** per-item final coverage survives synthesis and safety edits;
  invalid upstream results block dependents; independent tasks remain concurrent. Regression
  entry points are listed in the README. These tests are not a clinical grading substitute.
- **Verification commands:** run pytest, Ruff, offline CLI/API/Replay checks, and frontend build
  as applicable; record which commands actually ran. Older check results do not automatically
  certify the current revision or imply a fresh frontend build.
- **No private dependency:** public production code has no import, runtime path, subprocess
  bridge, or configuration dependency on the private runtime.
- **Provenance:** older textual-comparison reports retain their original scope; this
  documentation update does not rerun or certify private-source comparisons.

Real LLM and real Milvus checks for this A1-C1 documentation sync are `NOT_RUN`: they require operator-owned
credentials, services, models, and a lawfully obtained corpus. License and ownership
confirmation remain separate release-governance blockers, not native runtime wiring gaps.

Credential-pattern redaction is not patient-data de-identification; original audit events
may preserve model reasoning and case text. Never publish raw benchmark traces by default.
