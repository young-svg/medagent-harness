# MedAgent Harness

MedAgent Harness is a self-contained, traceable clinical-domain agent runtime.
Its public execution path is the **Centralized Planner–Worker Multi-Agent**
architecture implemented by `NativeMedAgentEngine`.

This project is an engineering demonstration, not a medical device. It must not
be used as a substitute for qualified clinical judgment.

## Runtime flow

```text
Case → Context → Contract + Ledger → Planner → Agent Runtime
     → Tools / RAG → Synthesis → Guardrail → Answer → Trace + Presentation
```

- The Answer Contract records requested deliverables.
- The Evidence Ledger records patient facts without turning them into medical knowledge.
- A centralized planner assigns work to Diagnostic, Consultation, and Research agents.
- Tool schemas are filtered before each model call and revalidated at execution time.
- Retrieval passes only admitted compact evidence to workers; the full bundle remains in trace data.
- Multi-agent results are synthesized, checked, and conservatively edited as stable units.
- Patient, Clinical, and Developer views share the exact native final answer.

Long-term memory is intentionally excluded from the stable public runtime. The
runtime keeps only bounded, process-local session context. No model weights,
clinical corpus, vector database, credentials, or third-party benchmark answers
are distributed in this repository.

## Quick start

Requires Python 3.11+.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# POSIX: source .venv/bin/activate
pip install -e ".[dev]"

medagent --help
medagent run examples/synthetic_case_1.json
medagent serve
```

The default is `MEDAGENT_RUNTIME_MODE=native`. Without endpoint configuration,
the engine uses a deterministic offline client suitable for installation and
workflow smoke tests. To use an OpenAI-compatible endpoint, set:

```text
MEDAGENT_LLM_BASE_URL=https://your-endpoint.example/v1
MEDAGENT_LLM_API_KEY=replace-me
MEDAGENT_LLM_MODEL=your-model
```

No real commercial API is called by the test suite.

### API

```bash
curl http://127.0.0.1:8000/health
curl -X POST http://127.0.0.1:8000/api/analyze \
  -H "Content-Type: application/json" \
  -d '{"description":"Synthetic example: intermittent fatigue","question":"What should be assessed?","session_id":"demo"}'
```

The response contains `final_answer`, `run_id`, `trace`, and `presentation`.
`presentation.professional_answer` is always identical to `final_answer`.

### Optional retrieval

`FakeRetrievalBackend` supports deterministic tests. `MilvusRetrievalBackend`
is an optional connection adapter and requires an application-supplied embedding
step plus `pip install -e ".[milvus]"`. Users must provide their own lawfully
obtained corpus and database.

### Frontend

```bash
cd web
npm ci
npm run build
```

The frontend exposes Patient, Clinical, and Developer views. Evidence Cards are
labeled **检索到的参考资料** and include only admitted items. They are references,
not a claim that a document proves a patient-specific conclusion. Developer View
shows Contract, Ledger, Plan, Route, Workers, Tool, Retrieval, Guardrail, latency,
and token metadata—not chain-of-thought.

## Validation

```bash
ruff check .
pytest -m "not integration"
```

CI uses Python 3.11, has no network requirement, and needs no LLM service,
retrieval service, or long-term-memory service.

## Evaluation status

Historical design-baseline metrics in `docs/evaluation.md` came from a separately
validated predecessor system. They are not measurements of this native engine.
The native implementation is ready for a fresh benchmark run only when the
criteria in `docs/NATIVE_BENCHMARK_READINESS.md` remain satisfied. This release
does not rerun or alter that benchmark.

## License status

The technical public-runtime boundary is complete, but license selection and
ownership confirmation remain user decisions. No legal-clearance claim is made.
See `LICENSE_PENDING.md` and `docs/OPEN_SOURCE_STATUS.md`.
