# Public Release Audit

## Current review: integration at `aba3dec` (2026-10-28)

**TECHNICAL_CHECKS_PASSED; PUBLICATION_AUTHORIZATION_PENDING**

This section supersedes unconditional release, privacy and license conclusions in
the historical snapshot below. This is a repository-evidence review, not legal
clearance or a complete privacy/provenance certification.

### Scope and secret/path findings

- Reviewed the 148 tracked files at `aba3dec`, manifests, declared fixtures and
  selected reachable Git-history artifacts. No historical untracked directory was
  inspected, and no raw local run was imported or published.
- `LICENSE_PENDING.md` and `docs/SECRET_AND_PATH_SCAN.md` are absent from this
  release tree. Their historical `main` versions were read from Git only; they
  were not restored. The old scan included an integration Trace fixture, whereas
  the current release tree contains no tracked JSONL Trace, runtime log, database,
  model weight or captured evaluation-output directory.
- Pattern checks found no credential-shaped key/private key or patient-identity
  field requiring disclosure. Three assignment candidates are explicit test-only
  redaction fixtures in `tests/test_runtime_claims.py`, not verified credentials.
- The path search excluded URL substrings. Its Windows-path candidate is the
  literal search-pattern example in this document, not a private machine path.
  Report candidates and exclusions rather than asserting that every absolute-path
  string, personal datum or secret in every revision is absent.
- No third-party source attribution header was found by the tracked-text search.
  Missing headers do not prove independent authorship or permission to republish.

### Public data and history boundary

- The CLI examples, four reference demo JSON files and RAG evidence cards are
  labeled synthetic. The frontend uses `web/src/demoData.ts` plus bundled answer
  and evidence JSON; it does not execute the reference JSON files as live runs.
- These labels and tests establish declared fixture behavior, not the origin or
  redistribution permission of every clinical paragraph. The owner must confirm
  authorship/provenance of case narratives and `web/src/demoFinalAnswers.json`.
- Benchmark pages contain aggregate historical claims, not raw case/answer dumps.
  The underlying case/output rights and permission to publish the comparison and
  product/model references still require owner confirmation; clinical results were
  not independently revalidated in this review.
- Deletions in the release tree did not rewrite Git history. Reachable ancestors
  still contain an integration Trace, screenshots, local-path reports and targeted
  evaluation question excerpts (for example the old `artifacts/integration-trace/`
  and `eval/requestspec_validation/` paths). The inspected integration Trace is a
  labeled deterministic synthetic run; the evaluation excerpts' redistribution
  permission is not established here. Do not equate a clean checkout with a
  sanitized history. No history rewrite or material deletion was performed.

### Dependency evidence, not a license grant

- Current installed metadata identifies MIT for FastAPI/Pydantic and BSD-3-Clause
  for HTTPX/Uvicorn; the optional retrieval dependencies declare Apache licensing.
  This is an environment snapshot, not a pinned Python transitive-dependency audit.
- The tracked npm lockfile declares MIT, Apache-2.0, ISC, BSD-3-Clause and MPL-2.0.
  The 12 MPL entries belong to Lightning CSS and its platform variants via Vite;
  they are not evidence that all variants or MPL code ship in browser assets.
- Confirm notices/source obligations against the actual source, bundle, container
  or binary being distributed. In particular, distinguish build-tool use from
  redistribution of MPL-covered code; do not assume the whole project must adopt
  MPL. See the [Mozilla MPL FAQ](https://www.mozilla.org/en-US/MPL/2.0/FAQ/).
- No project license or third-party notice file was added. Publication authority
  for employer/university/collaborator work, imported prompts/code and demo text
  cannot be inferred from dependency licenses or a technical clean-room claim.

The preceding integration verified 423 tests, Ruff, frontend build and offline
Smoke. This review changes documentation only and does not claim a fresh test run,
real-model/Milvus evaluation, exhaustive dependency audit or clinical validation.

## Historical snapshot (not current release clearance)

## Release Candidate

- Target branch: `release/github-ready`
- Audited release base commit: `600e37aec021d2e049c9fff610921d847d238032`
- Frozen runtime parent: `5e0adaaa1f209f43ee531a9b176bb94c8056a692`
- Audit scope: tracked repository, documentation claims, public dependencies, reproducibility, and release metadata
- Runtime, planner, workers, routing, prompts, recovery logic, and benchmark outputs were not changed.

## Secrets & Privacy

**SECRET_PRIVACY = PASS**

- No tracked `.env` file, credential value, API key, token, password, private key, or populated Authorization header was detected.
- `.env.example` contains names and blank placeholders only.
- No raw trace, request-header log, patient dataset, benchmark input dump, or private clinical dataset is tracked.
- Demo cases, identifiers, and evidence cards are explicitly synthetic.

## Local Paths

**LOCAL_PATH = PASS**

- No tracked `E:\`, `C:\`, `/home/`, private repository, or private model path is present.
- Localhost addresses are intentional local API/CLI examples. The current static
  frontend has no Vite API proxy and makes no backend inference calls.
- Test endpoints use reserved `.invalid` hosts and do not represent private services.

## Private Dependencies

**PRIVATE_DEPENDENCY = PASS**

- No `swarm`, `legacy_v2`, `medix-agent-swarm`, private subprocess bridge, private repository path dependency, or private runtime import was found.
- Core imports resolve within the public package or declared public dependencies.
- The deterministic local path does not require the former private repository.

## Data / RAG

**DATA_RAG = PASS**

- No Milvus database, model weight, medical knowledge-base file, raw MedicalQA-DX corpus, raw guideline corpus, patient dataset, or private benchmark source dataset is tracked.
- Milvus is an optional backend with optional dependencies.
- Users must supply and govern their own corpus; the repository does not distribute a local medical knowledge base.
- The included RAG cards are synthetic UI/test fixtures and are not described as a verified authoritative guideline database.
- The `MedicalQA-DX` string appears only as a synthetic unit-test source label; no source content is included.

## License / Attribution

**LICENSE_RELEASE_STATUS = PUBLIC_VIEWING_AND_EVALUATION_ONLY**

- The repository does not grant an open-source license.
- The standalone project license file has been removed.
- No third-party license header, copied-code notice, or obvious file-level attribution requirement was detected in tracked source files.
- Public dependencies and GitHub Actions remain third-party projects referenced through manifests/workflows rather than copied source.
- Third-party components remain subject to their respective licenses.

## Benchmark Claims

**BENCHMARK_CLAIMS = PASS**

- Reliability is reported as 60/60 completed valid executions with zero tool replay.
- The methodology defines no-content transient Provider failures as infrastructure-invalid attempts that may be repeated only with identical inputs; valid responses cannot receive quality-triggered reruns.
- The comparison reports 60 matched cases, MedAgent 4.6733, DeepSeek Web 4.3883, delta +0.2850 (+6.49%), and 52/8/0 wins/losses/ties.
- DeepSeek Web is described as a saved product-output baseline.
- Both systems are documented as using the same underlying DeepSeek model; the result is framed as a paired complete-system comparison, not a strict component-level causal ablation or proof that the +6.49% delta is solely caused by the Harness.
- OCAS / Overall Clinical Quality is explicitly project-defined, not an official MedCli score.
- The wording is limited to higher clinical decision-support quality on this fixed evaluation set and disclaims independent clinical validation.

## README Links

**README_LINKS = PASS**

- All relative links resolve to tracked files.
- README links Architecture, Benchmark, Reliability, Failure Analysis, Design, and Demo documentation.
- No deleted artifact, screenshot, trace, or obsolete document is referenced.
- Setup, API, frontend, test, and demo commands refer to existing files and entry points.

## Reproducibility

**REPRODUCIBILITY = PASS**

- The base install, deterministic CLI/runtime, API, tests, and synthetic demos do not require the dirty main worktree, a private repository, a real provider, or a medical knowledge base.
- The optional RAG boundary fails explicitly when its extra dependencies or user-managed configuration are absent.
- A fresh single-branch clone passed package import, the full Python test suite, Ruff, and the production frontend build without provider calls.

## Git Hygiene

**GIT_HYGIENE = PASS**

- No tracked cache, `node_modules`, frontend `dist`, artifact, log, checkpoint, model, database, IDE metadata, temporary JSON dump, raw trace, or local configuration file was found.
- `.gitignore` covers `.env`, Python/pytest/Ruff caches, Node/Vite outputs, artifacts, logs, traces, checkpoints, databases, models, datasets, corpora, and generated JSONL rows.
- The final release tree contains 132 tracked files and no file larger than the frontend lockfile.

## Tests

- `pytest`: 172 passed.
- `ruff check medagent tests`: passed.
- `npm run build`: passed.
- `git diff --check`: passed.
- `import medagent`: passed in the fresh clone.
- No provider, retrieval service, or benchmark was called.

## Final Verdict

**HISTORICAL_TECHNICAL_READINESS_ONLY**

The old technical audit does not authorize publication of the integrated tree or
its reachable history. No open-source license is granted. Current owner decisions
and third-party obligations remain pending as described above.

## USER_ACTION_REQUIRED

- Confirm ownership and authority to publish all source, prompts and demo text,
  including any employer, university or collaborator interests.
- Confirm the intended public-viewing terms; select an open-source license only if
  the rights holders explicitly authorize it. Missing an open-source license is
  not itself a finding that the owner cannot publish their own work.
- Confirm permissions for reachable historical evaluation excerpts and other old
  materials; decide whether any separate release/history action is required.
- Review third-party notices and source obligations for actual distributed assets.
