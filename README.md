# MedAgent Harness

## Overview

A multi-agent medical decision-support system that improves complex clinical reasoning through task decomposition, specialized agents, verification, and reliability control.

MedAgent Harness is an engineering project for traceable clinical-domain workflows. It is not a medical device and does not replace qualified clinical judgment.

## Why this project

Complex clinical questions often hide several deliverables inside one prompt: identify the likely diagnosis, explain the evidence, compare alternatives, recommend tests, propose treatment, and define follow-up. MedAgent turns those requirements into an explicit execution contract and checks that the final response covers them.

Key capabilities:

- deterministic request decomposition with `RequestSpec`;
- complexity-aware single-agent or multi-agent routing;
- specialized Diagnostic, Consultation, and Research agents;
- bounded tool use and optional evidence retrieval;
- answer-contract and request-coverage verification;
- bounded structured-output protocol recovery;
- observable traces without exposing hidden chain-of-thought;
- offline deterministic fixtures for local development and CI.

## Architecture

```text
User Query
    |
    v
Request Understanding (RequestSpec + AnswerContract)
    |
    v
Complexity Router
    |
    +--------------------+
    |                    |
    v                    v
Single Agent         Multi Agent
    |                    |
    +----------+---------+
               |
               v
   Diagnostic / Consultation / Research Agents
               |
               v
        Tools / Retrieval
               |
               v
     Answer Contract Verification
               |
               v
         Final Response
```

- **RequestSpec** prevents explicit user requirements from disappearing inside a broad clinical task.
- **Multi-agent routing** separates independently useful diagnostic, consultation, and evidence work when one long generation would be brittle.
- **Answer Contract verification** checks required diagnosis, tests, treatment, and follow-up components where the request calls for them.
- **Protocol Recovery** repairs a malformed worker serialization at most once, without rerunning the planner, replaying tools, or triggering a quality retry.

See [Architecture](docs/ARCHITECTURE.md) and [Design](docs/DESIGN.md) for the engineering rationale.

## Repository layout

```text
api/                 FastAPI boundary
docs/                Architecture, evaluation, reliability, and demo documentation
eval/                Reusable evaluation schemas and scoring utilities
examples/demo_cases/ Synthetic showcase fixtures
medagent/            Planner, runtime, agents, tools, retrieval, and verification
scripts/             Public script policy and extension point
tests/               Deterministic unit and integration-contract tests
web/                 React/Vite frontend
```

The package keeps its established module layout to avoid import churn in a release-only cleanup.

## Quick start

Requires Python 3.11+.

```bash
python -m venv .venv
# POSIX: source .venv/bin/activate
# Windows: .venv\Scripts\activate
pip install -e ".[dev]"

medagent run examples/synthetic_case_1.json
medagent serve
```

Without an external model endpoint, the project uses its deterministic local client for workflow smoke tests. Copy `.env.example` only when configuring an OpenAI-compatible endpoint; never commit `.env`.

### API

```bash
curl http://127.0.0.1:8000/health
curl -X POST http://127.0.0.1:8000/api/analyze \
  -H "Content-Type: application/json" \
  -d '{"description":"Synthetic example: intermittent fatigue","question":"What should be assessed?","session_id":"demo"}'
```

### Frontend

```bash
cd web
npm ci
npm run build
npm run dev
```

The frontend includes four synthetic demonstrations: single-agent routing, multi-agent collaboration, synthetic RAG, and same-session memory. See [Demo guide](docs/DEMO.md).

## Evaluation

### Reliability Benchmark

Fixed 60 clinical cases:

| Metric | Result |
| --- | ---: |
| Completion | 59/60 |
| Non-empty answers | 59/60 |
| Tool replay | 0 |
| Protocol recovery | Validated |

This is a fixed development reliability benchmark, not an unseen test set. It measures execution reliability, not general medical capability. See [Reliability](docs/RELIABILITY.md).

## Comparison with DeepSeek Web

The comparison used 60 matched clinical cases and a fixed clinical decision-support rubric.

| Metric | MedAgent | DeepSeek Web |
| --- | ---: | ---: |
| Overall Clinical Quality | 4.6733 | 4.3883 |
| Medical | 4.8333 | 4.3333 |
| Completeness | 4.7833 | 4.0667 |
| Workflow | 4.7833 | 4.0833 |
| Safety | 4.8833 | 4.4833 |

On this fixed benchmark, MedAgent achieved higher overall clinical decision-support quality, mainly through better requirement coverage, a more structured clinical workflow, and safety-aware reasoning. The result is benchmark-specific and does not support broad claims about general medical ability. See [Benchmark](docs/BENCHMARK.md).

## Validation

```bash
pytest
ruff check medagent tests

cd web
npm ci
npm run build
```

CI runs offline and does not require a model endpoint, vector database, patient dataset, or private corpus.

## Public-release data policy

This repository does not distribute patient records, benchmark case text, candidate answer dumps, private medical corpora, vector databases, model weights, credentials, or local traces. Demo inputs and evidence cards are synthetic fixtures.

## License

Released under the [MIT License](LICENSE).
