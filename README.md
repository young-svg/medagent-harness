# MedAgent Harness

MedAgent Harness is a self-contained, traceable clinical-domain agent runtime. Its
public execution path is the centralized Planner-Worker architecture implemented by
`NativeMedAgentEngine`.

This project is an engineering demonstration, not a medical device. It must not be
used as a substitute for qualified clinical judgment.

## Runtime flow

```text
Case -> Context + bounded memory -> Contract + Ledger -> Planner -> Workers
     -> Tools / optional RAG -> Synthesis -> Guardrail -> Answer -> Trace + Presentation
```

- Bounded, process-local session memory is injected into the actual Planner and Worker
  messages. Current description/question and the current Contract/Ledger remain
  authoritative over history; sessions are isolated. Applications can explicitly disable
  process-local history with `SessionMemory(enabled=False)`.
- The four public procedural specs in `medagent/skills/specs/` are loaded at startup and
  injected into the corresponding Diagnostic, Consultation, Research, and Synthesis
  system messages. Trace metadata records each skill name and SHA-256 digest.
- Tool schemas are filtered before every Worker model call and revalidated at execution.
- Retrieval mode is selected by configuration: `off`, deterministic `fake`, or optional
  `milvus`. Only admitted compact evidence reaches model context; raw candidates,
  admission decisions, collection routing, and compact evidence are traced.
- Every Planner, Worker, and Synthesis model boundary records actual messages, tool
  schemas, model settings, content/tool calls, usage, finish reason, latency, and errors.
  Central trace redaction removes credential-shaped values without deleting normal
  clinical text.
- Multi-agent drafts are synthesized, checked, and conservatively edited as stable units.
- Patient, Clinical, and Developer views share the exact native final answer.

Long-term memory is intentionally excluded. No model weights, clinical corpus, vector
database, credentials, or third-party benchmark answers are distributed here.

## Quick start: offline smoke

Requires Python 3.11+.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# POSIX: source .venv/bin/activate
pip install -e ".[dev]"

# This is the default; shown explicitly for reproducibility.
MEDAGENT_RETRIEVAL_MODE=off medagent run examples/synthetic_case_1.json
medagent --help
medagent serve
```

On Windows PowerShell, set an environment value with
`$env:MEDAGENT_RETRIEVAL_MODE = "off"`. With no model endpoint configured, the runtime
uses a deterministic local client for installation and workflow smoke tests. It does not
make a commercial API call.

## Real OpenAI-compatible model endpoint

Set the following variables before running the CLI or API:

```text
MEDAGENT_LLM_BASE_URL=https://your-endpoint.example/v1
MEDAGENT_LLM_API_KEY=replace-me
MEDAGENT_LLM_MODEL=your-model
```

Temperature and the generic client fallback retain their documented defaults through
`MEDAGENT_LLM_TEMPERATURE` and `MEDAGENT_LLM_MAX_TOKENS`. Planner, Worker, and Synthesis
generation use independent ceilings through `MEDAGENT_PLANNER_MAX_TOKENS`,
`MEDAGENT_WORKER_MAX_TOKENS`, and `MEDAGENT_SYNTHESIS_MAX_TOKENS` (each defaults to `8192`).
Each stage allows at most one same-input completion recovery after an unusable
`finish_reason=length` response. The corresponding settings are
`MEDAGENT_PLANNER_MAX_LENGTH_RECOVERIES`, `MEDAGENT_WORKER_MAX_LENGTH_RECOVERIES`, and
`MEDAGENT_SYNTHESIS_MAX_LENGTH_RECOVERIES` (each defaults to `1`). These are output ceilings,
not fixed consumption targets; normal `stop` responses use only their actual tokens.

## Clinical RAG configuration

Offline-safe modes require no optional dependency:

```text
MEDAGENT_RETRIEVAL_MODE=off   # retrieval calls fail explicitly
MEDAGENT_RETRIEVAL_MODE=fake  # deterministic tests/fixtures
```

For a real Milvus deployment:

```bash
pip install -e ".[retrieval]"
```

```text
MEDAGENT_RETRIEVAL_MODE=milvus
MEDAGENT_MILVUS_URI=https://your-milvus-endpoint.example
MEDAGENT_MILVUS_TOKEN=replace-me
MEDAGENT_EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5
MEDAGENT_GENERIC_COLLECTION=clinical_knowledge
MEDAGENT_SPECIAL_COLLECTION=clinical_guidelines
MEDAGENT_RETRIEVAL_TOP_K=5
MEDAGENT_RETRIEVAL_THRESHOLD=0.63
```

Milvus mode validates URI, embedding configuration, and optional packages. Missing
configuration fails clearly; it never silently falls back to fake retrieval. Users must
provide and govern their own lawfully obtained clinical corpus and database.

## API

```bash
curl http://127.0.0.1:8000/health
curl -X POST http://127.0.0.1:8000/api/analyze \
  -H "Content-Type: application/json" \
  -d '{"description":"Synthetic example: intermittent fatigue","question":"What should be assessed?","session_id":"demo"}'
```

The response contains `final_answer`, `run_id`, `trace`, and `presentation`.
`presentation.professional_answer` is always identical to `final_answer`.

## Frontend and trace replay

```bash
cd web
npm ci
npm run build
```

Patient, Clinical, and Developer views are provided. Evidence Cards contain only admitted
references and do not claim patient-specific proof. Large structured trace content is
collapsed by default in Developer View; it exposes observable inputs/outputs, not hidden
chain-of-thought.

Every run writes `runs/<run_id>/trace.jsonl` by default. Replay reads the recorded final
answer without invoking a model:

```bash
medagent replay runs/<run_id>
```

## Validation

```bash
ruff check .
pytest -m "not integration"
```

The public tests need no model endpoint, Milvus service, or long-term-memory service.
`tests/test_runtime_claims.py` prevents the documented Memory, Skills, RAG, and full Trace
capabilities from becoming disconnected files or dead code.

## Evaluation and license status

Historical design-baseline metrics in `docs/evaluation.md` are not measurements of this
native engine. Readiness for a future rerun is documented in
`docs/NATIVE_BENCHMARK_READINESS.md`; this release does not rerun or alter the benchmark.

License selection and ownership confirmation remain user decisions. No legal-clearance
claim is made. See `LICENSE_PENDING.md` and `docs/OPEN_SOURCE_STATUS.md`.
