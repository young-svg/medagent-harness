# MedAgent Harness v1 rebuild report

Generated: 2026-09-20 22:12 Asia/Hong_Kong

## 1. Target repository

Final local path: `E:/agent/medagent-harness`

The requested target was absent, so no conflict suffix was needed. The
repository is local only; no remote was added and nothing was pushed.

## 2. Source license status

`REDISTRIBUTION_LICENSE_UNVERIFIED`. The source root had no `LICENSE`,
`COPYING`, or `NOTICE` instrument. A README license sentence was not treated as
sufficient authorization for whole-repository redistribution. Publication is
blocked by `PUBLISH_BLOCKED_LICENSE_UNVERIFIED` pending owner/institutional
confirmation. No MIT or Apache license was added automatically.

## 3. Copied versus reimplemented

Historical experiments, the old README, benchmark/reference data, clinical
corpora, local logs, secrets, and third-party/institutional material were not
copied. Modules were cleanly reimplemented from the recorded freeze-v2 behavior
contract. Only factual freeze metadata (configuration values and SHA-256
identities) was transcribed for provenance.

## 4. Final architecture

The project uses contract-driven context, an Evidence Ledger, centralized
Planner–Worker routing, Diagnostic/Consultation/Research workers, role-filtered
tools, deterministic query construction, configurable collection routing,
unified evidence admission, bounded Session Memory, centralized synthesis,
conservative checker/Stable-ID patch/sanitizer handling, PresentationAdapter,
and JSONL Trace/Replay. The React demo exposes Patient, Clinical and Developer
views without showing private chain-of-thought.

## 5. Freeze-v2 behavior mapping

- Centralized single/multi route and deterministic planner fallback: implemented
- Three frozen worker roles: implemented
- Tool allowlist/capability filter and two-call budget: implemented and tested
- Generic `medical_knowledge_v1`; specialized `medical_knowledge`: configurable
- Unified EvidenceItem/admission and compact worker evidence: implemented
- Session memory: enabled, trimmed, exact-adjacent deduplication
- Long-term memory/Mem0: disabled, no provider initialized or tool exposed
- Conservative checker/Stable-ID patch/sanitizer boundary: implemented
- Trace v2 public lifecycle and 180-second worker timeout: implemented
- Scope Controller v3 compatibility gate: documentation only, not production

## 6. UI status

Implemented with React, TypeScript and Vite. `npm ci` passed; the production
build passed with Vite 8.3.0 (16 modules, 223.96 kB main JavaScript before
gzip). Dependency audit reported zero vulnerabilities.

## 7. API and CLI status

FastAPI endpoints `GET /health`, `POST /api/analyze`, and
`GET /api/runs/{run_id}` are implemented. CLI commands `run`, `serve`, `trace`,
and `replay` are implemented. Import, help, synthetic run, trace reader and
replay smoke checks passed.

## 8. Tests and lint

- Python: 3.12.7 in an isolated project environment
- Editable install with development extras: passed
- `ruff check .`: passed
- `pytest -m "not integration"`: 16 passed, 0 failed
- Two upstream FastAPI/Starlette TestClient deprecation warnings were observed
- Default validation used no live LLM, Mem0, remote Milvus or internet

## 9. CI

GitHub Actions is configured for Python 3.11, editable development install,
ruff, offline pytest, Node setup, `npm ci`, and the frontend build. The local
equivalents passed; hosted GitHub Actions was not run because no remote was
created.

## 10. Secret scan

Zero real credentials, zero credential-shaped values, and zero user-specific
absolute paths were found in the intended public source at final scan. The only
secret-variable match was the empty `MEDAGENT_LLM_API_KEY=` placeholder and the
scan report describing it.

## 11. Known limitations

Offline mode demonstrates orchestration but deliberately generates no new
clinical facts. A licensed/validated clinical corpus and deployment-specific
retrieval/model adapters are not distributed. Session state is in-process.
Guardrails do not guarantee medical correctness. The fixed development metrics
are not unseen-holdout or clinical-safety claims.

## 12. GitHub publish blockers

The only hard publish blocker is unverified redistribution/license authority.
Before publication, confirm ownership/authorization, select the license, add
required notices, rerun host secret scanning, and execute hosted CI.

## 13. Review bundle

- Archive: `E:/agent/medagent-harness_review_20260920_222334.zip`
- SHA-256: `4bc63710843b49e5bd39770d060cd0c487ba07f102c5e1fb5e76bb438d47aa13`
- Size: 67,900 bytes
- Archived Git commit: `3c3cf91b345d922ffd6258a630788035d0ce208d`

The archive is a `git archive` of the validated source commit, so it excludes
`.git`, ignored environments, `node_modules`, `dist`, databases, models,
runtime traces and private benchmark artifacts. This companion report was
created after hashing and is intentionally outside the archive to avoid a
self-referential checksum.
