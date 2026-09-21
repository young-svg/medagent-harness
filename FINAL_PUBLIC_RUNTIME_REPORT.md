# Final Public Runtime Report

1. **NativeMedAgentEngine:** complete. It exposes async `analyze` and `close`,
   accepts description/question/session ID, and returns final answer, run ID,
   trace summary, and presentation payload.
2. **Non-public runtime dependency:** none. Native execution has no import,
   dynamic path injection, subprocess bridge, or runtime-path configuration for
   a non-public codebase.
3. **Obsolete runtime packages/adapters:** removed from production, tests,
   documentation, and packaging metadata.
4. **Contract / Ledger:** independently implemented with validation,
   deterministic fallbacks, multi-deliverable support, stable findings, and
   source-conflict representation.
5. **Planner / Router:** structured JSON parse, missing/invalid agent fallback,
   dispatchability validation, and single/multi routing are implemented.
6. **AgentLoop / Workers:** async Diagnostic, Consultation, and Research worker
   roles are implemented with filtered tool schemas, execution-time validation,
   a default two-call budget, timeouts, and observable worker results.
7. **Skills:** four short public role specifications define scope, required
   output behavior, tool permissions, and safety boundaries.
8. **RAG:** deterministic query building, collection routing, evidence models,
   admission, compact worker payloads, `FakeRetrievalBackend`, and optional
   `MilvusRetrievalBackend` are implemented. Full bundles are trace-only.
9. **Memory:** bounded, deduplicated, session-isolated local memory is complete;
   current input has priority. Long-term memory is intentionally excluded.
10. **Guardrail:** public behavior covers patient-fact support boundaries,
    recommendation/completion distinction, history/current distinction,
    questions, conditions, same-target contradiction alignment, safe-edit
    eligibility, and mixed-unit protection.
11. **Trace:** the public event schema, required lifecycle events, explicit
    parent IDs, retrieval parent chain, JSONL persistence, summary, and replay
    reader are complete.
12. **Presentation / API / UI:** Patient, Clinical, and Developer payloads,
    FastAPI health/analyze/run routes, CLI, and React frontend are operational.
    The professional answer is exactly the engine final answer; only admitted
    references become Evidence Cards.
13. **Public tests:** 14 passed, 0 failed, 0 skipped. Ruff passed. Editable
    package installation, CLI help/case, FastAPI health/analysis, and frontend
    production build passed without a model API, retrieval service, or network
    at runtime.
14. **Semantic parity:** three synthetic scenarios completed. Single routing,
    multi routing/synthesis/tool budget, and correction propagation match at the
    behavior boundary. Intentional representation differences are acceptable;
    regressions: 0.
15. **Provenance audit:** 58 public production files were compared with 121
    behavior-oracle files. No 200-character match, eight-line match, long prompt
    match, origin marker, or non-trivial identical line remained.
16. **Secret/path scan:** zero real secrets, user-specific absolute paths,
    non-public module references, dynamic path injection, and execution bridges.
17. **License status:** `LICENSE_PENDING.md` remains. No license was selected and
    no legal-clearance claim is made.
18. **Native benchmark readiness:** YES for a later rerun; no benchmark was
    rerun or modified in this work.
19. **Remaining blockers:** license/ownership confirmation before describing the
    project as legally open source. Real endpoint, corpus, embedding, and Milvus
    configuration remain deployment responsibilities, not public-runtime blockers.
20. **Review archive:** sibling file
    `../medagent-native-public-review_20260921_112717.zip`; SHA-256
    `cd455a79519162cfd15afbea617be7bd2fe81e624ec98ab4891294a4c74f5f37`.
    The archived report records the pre-packaging placeholder because an archive
    cannot contain its own final digest; this post-packaging working-tree copy is
    the authoritative digest record.
