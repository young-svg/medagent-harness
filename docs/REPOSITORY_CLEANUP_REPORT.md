# Repository Cleanup Report

## Release baseline

- Branch: `release/github-ready`
- Frozen base: `5e0adaaa1f209f43ee531a9b176bb94c8056a692`
- Core runtime behavior: unchanged

## A. Retained

All retained tracked files fall into these public project groups:

- `.github/workflows/` — offline CI for Python and frontend validation;
- `.env.example`, `.gitattributes`, `.gitignore`, `pyproject.toml`, `requirements.txt` — public configuration and packaging metadata;
- `api/` — FastAPI boundary and schemas;
- `medagent/` — context, planning, agents, runtime, tools, retrieval abstraction, guardrails, memory, presentation, observability, LLM boundary, and public skill specifications;
- `tests/` — deterministic runtime, recovery, routing, retrieval-contract, presentation, and guardrail tests;
- `web/` — React/Vite frontend and synthetic display fixtures;
- `examples/` — synthetic CLI cases and four synthetic showcase fixtures;
- `eval/README.md`, `eval/schema.json`, `eval/benchmark/`, `eval/retrieval/`, and `eval/routing/` — reusable, data-free evaluation utilities;
- `scripts/README.md` — policy for future public helper scripts;
- `docs/` — release architecture, design, benchmark, reliability, failure, demo, and cleanup documentation;
- `README.md` — public project overview and public viewing/evaluation terms.

## B. Reorganized or rewritten

- Replaced the historical README with a five-minute project overview, architecture, setup, evaluation, and safety boundaries.
- Consolidated fragmented implementation and audit notes into stable public documents.
- Added a conventional `requirements.txt` while retaining `pyproject.toml` as the package source of truth.
- Expanded `.gitignore` for artifacts, logs, checkpoints, databases, model files, corpora, data, and generated evaluation rows.
- Replaced captured private-corpus RAG previews with synthetic evidence fixtures and updated their tests and labels.
- Kept the established package and top-level `eval/` layout to avoid import churn.

## C. Removed experimental material

- `artifacts/` — four browser screenshots, one local integration trace, validation logs, and JUnit output;
- historical implementation/audit reports under `docs/`;
- `eval/requestspec_validation/` targeted-validation outputs and runner;
- `eval/run_native_real_validation.py`, an environment-specific benchmark runner;
- `scripts/run_native_medicalqa_smoke.py`, a local private-corpus smoke runner;
- `FINAL_PUBLIC_RUNTIME_REPORT.md`, an internal handoff report containing a local path;
- `LICENSE_PENDING.md`, removed after release terms were consolidated in the README.

## Privacy and release checks

- No `.env`, API key, provider endpoint, local absolute path, model weight, database, patient dataset, private medical corpus, benchmark answer dump, screenshot, log, or local trace is included in the release tree.
- Demo cases and RAG evidence are synthetic fixtures.
- Benchmark documentation contains aggregate metrics only.

## Size

- Frozen baseline tracked working-tree bytes: 2,011,590.
- Final tracked working-tree size: approximately 547 KB, a 72.8% reduction.
- Git object history is not rewritten; historical objects remain reachable from the base commit.
