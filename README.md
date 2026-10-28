# MedAgent Harness

MedAgent Harness is a self-contained, traceable clinical-domain agent runtime. Its
public execution path is the centralized Planner-Worker architecture implemented by
`NativeMedAgentEngine`.

The technical focus is explicit task ownership, dependency-aware execution, and
verifiable completion: independent work runs concurrently; dependent work consumes
sanitized, source-linked upstream answers before it starts. This is not an autonomous
agent swarm or a clinically validated decision system.

This project is an engineering demonstration, not a medical device. It must not be
used as a substitute for qualified clinical judgment.
Real clinical deployment, patient safety, and medical effectiveness have not been
validated by the current engineering regression suite.

## Runtime flow

```text
Case -> RequestSpec + Contract + Ledger + budgeted session history -> Planner
     -> validated dependency graph -> independent Workers in parallel
     -> dependent Workers after validated Handoff -> Worker coverage
     -> structured Synthesis (multiple successful Workers only)
     -> deterministic recovery + sanitizer + Guardrail edits
     -> final per-item coverage -> Answer + Trace + Presentation
```

- Bounded, process-local session memory is injected into the actual Planner and Worker
  messages. Current description/question and the current Contract/Ledger remain
  authoritative over history; sessions are isolated. Applications can explicitly disable
  process-local history with `SessionMemory(enabled=False)`.
- `depends_on` defaults to `[]`. Duplicate task IDs, unknown references, self-dependencies,
  and cycles are rejected. Dependency plans preserve task IDs instead of collapsing or
  merging same-role work; unsafe contract-policy removal fails explicitly. Each task
  waits only for its dependencies, not an entire scheduling wave. Worker timeouts cover
  execution after dependencies are ready. Results retain plan order.
- Handoff contains upstream task/Worker IDs, sanitized `RequestItemAnswer` values,
  success status, and the source `worker_draft` event ID. It is separate from patient
  facts and session memory. Failed, timed-out, or unusable upstream answers block
  downstream model calls and prevent `completed`.
- The four public procedural specs in `medagent/skills/specs/` are loaded at startup and
  injected into the corresponding Diagnostic, Consultation, Research, and Synthesis
  system messages. Trace metadata records each skill name and SHA-256 digest.
- Tool permissions, registration, shared per-Worker budget, and arguments are checked
  before a handler runs. Validation follows the same published flat object/string
  schemas, including required fields and `additionalProperties`; empty strings and
  lengths are restricted only when the schema declares those constraints. Rejected
  arguments consume an attempted call, while permission/registration/budget denials do not.
- Retrieval mode is selected by configuration: `off`, deterministic `fake`, or optional
  `milvus`. Only admitted compact evidence reaches model context; raw candidates,
  admission decisions, collection routing, and compact evidence are traced.
- Every Planner, Worker, and Synthesis model boundary records actual messages, tool
  schemas, model settings, content/tool calls, usage, finish reason, latency, and errors.
  Known causal parents are explicit; absent parents remain null rather than linking to
  another concurrent Worker's last event. See the trace privacy boundary below.
- Multi-Worker synthesis returns answers keyed by `request_item_id`. Missing or malformed
  synthesis is repaired from corresponding validated Worker answers without another LLM
  call. Answers are rendered in RequestSpec order and rechecked after sanitization and
  Guardrail edits. Worker coverage and final-answer coverage are distinct; Guardrail
  `check.passed` alone does not decide completion. These are structural, not semantic
  medical-correctness guarantees.
- Patient, Clinical, and Developer views share the exact native final answer.

Long-term memory is intentionally excluded. No model weights, clinical corpus, vector
database, credentials, or third-party benchmark answers are distributed here.

Implementation and offline regression evidence:

| Capability | Implementation | Regression tests |
| --- | --- | --- |
| Tool argument validation (A1) | [registry](medagent/tools/registry.py), [schemas](medagent/tools/schemas.py) | [argument validation](tests/test_tool_argument_validation.py) |
| Final coverage and recovery (A2) | [final answers](medagent/runtime/final_answers.py), [coverage](medagent/runtime/coverage.py) | [final coverage](tests/test_final_answer_coverage_audit.py) |
| Concurrent causal Trace (A3) | [recorder](medagent/observability/tracer.py), [AgentLoop](medagent/runtime/agent_loop.py) | [causality](tests/test_trace_causality.py) |
| Subtask-aligned RAG (B1) | [query builder](medagent/retrieval/query_builder.py), [runtime](medagent/runtime/native_engine.py) | [query alignment](tests/test_retrieval_query_alignment_audit.py) |
| Estimated Memory budget (B2) | [session memory](medagent/memory/session.py), [config](medagent/runtime/config.py) | [memory budget](tests/test_session_memory_budget_audit.py) |
| Dependency scheduling / Handoff (C1) | [planner](medagent/planning/planner.py), [handoff](medagent/runtime/handoff.py) | [dependency and sanitization tests](tests/test_multi_agent_handoff_audit.py) |

For a compact dependency-plan example and operating limits, see
[architecture](docs/architecture.md) and the [behavior specification](docs/NATIVE_IMPLEMENTATION_SPEC.md).

## Quick start: offline smoke

Requires Python 3.11+.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# POSIX: source .venv/bin/activate
pip install -e ".[dev]"

# POSIX: explicitly select an offline smoke, even if this shell previously used an API.
export MEDAGENT_RUNTIME_MODE=native
export MEDAGENT_LLM_BASE_URL=
export MEDAGENT_LLM_API_KEY=
export MEDAGENT_RETRIEVAL_MODE=off
export MEDICAL_KB_ENABLED=false
medagent run examples/synthetic_case_1.json
medagent --help
medagent serve
```

On Windows PowerShell, set the equivalent environment values before those commands:

```powershell
$env:MEDAGENT_RUNTIME_MODE = "native"
$env:MEDAGENT_LLM_BASE_URL = ""
$env:MEDAGENT_LLM_API_KEY = ""
$env:MEDAGENT_RETRIEVAL_MODE = "off"
$env:MEDICAL_KB_ENABLED = "false"
```

With no model endpoint configured, the runtime
uses a deterministic local client for installation and workflow smoke tests. It does not
perform medical inference or make a commercial API call. `DeterministicLLM` does not
demonstrate learned planning, dependency discovery, or tool selection; use the offline
ScriptedLLM regressions to reproduce those control-flow boundaries. A CLI invocation
creates and closes its own engine; use a long-lived engine/API for multi-turn sessions.

## Real OpenAI-compatible model endpoint

Set the following variables before running the CLI or API:

```text
MEDAGENT_LLM_BASE_URL=https://your-endpoint.example/v1
MEDAGENT_LLM_API_KEY=replace-me
MEDAGENT_LLM_MODEL=your-model
```

Both a non-empty base URL and API key are required to select the real API client;
otherwise `_build_llm()` selects `DeterministicLLM`. Check your configuration rather
than treating a successful response as proof of real inference. Values above are
placeholders, not credentials. The CLI/API read process environment variables; they
do not automatically load `.env`. Export values yourself. Retrieval is a separate switch.

Temperature and the generic client fallback retain their documented defaults through
`MEDAGENT_LLM_TEMPERATURE` and `MEDAGENT_LLM_MAX_TOKENS`. Planner, Worker, and Synthesis
generation use independent ceilings through `MEDAGENT_PLANNER_MAX_TOKENS`,
`MEDAGENT_WORKER_MAX_TOKENS`, and `MEDAGENT_SYNTHESIS_MAX_TOKENS` (each defaults to `8192`).
Each stage allows at most one same-input completion recovery after an unusable
`finish_reason=length` response. The corresponding settings are
`MEDAGENT_PLANNER_MAX_LENGTH_RECOVERIES`, `MEDAGENT_WORKER_MAX_LENGTH_RECOVERIES`, and
`MEDAGENT_SYNTHESIS_MAX_LENGTH_RECOVERIES` (each defaults to `1`). These are output ceilings,
not fixed consumption targets; normal `stop` responses use only their actual tokens.

Additional controls from [RuntimeConfig](medagent/runtime/config.py):

| Variable | Default | Meaning |
| --- | --- | --- |
| `MEDAGENT_WORKER_MAX_INFRA_RETRIES` | `1` | `0` or `1` transient infrastructure retry per Worker generation |
| `MEDAGENT_MAX_TOOL_CALLS` | `2` | Shared per-Worker tool attempt limit in one run |
| `MEDAGENT_LLM_TIMEOUT_SECONDS` | `60` | Real model client request timeout |
| `MEDAGENT_WORKER_TIMEOUT_SECONDS` | `180` | Per-task execution timeout, excluding dependency waiting |
| `MEDAGENT_MEMORY_TOKEN_BUDGET` | `4096` | Estimated history budget; `0` excludes history, `None` is unlimited |
| `MEDAGENT_TRACE_DIR` | `runs` | Engine/API trace directory; CLI `run --trace-root` overrides it (default `runs`) |

Memory also keeps the existing message-count limit (`SessionMemory.recent_limit`, default
`10`, reserving one slot for current input). Reads drop whole oldest historical messages
until both limits fit. Estimation charges UTF-8 bytes of history JSON plus eight per
message; it includes roles and serialization, but is **not a real-token count or a hard
model-window limit**. Current input and task context are never budget-truncated.
Budget `0` still permits storage; `enabled=False` suppresses both reads and writes without
clearing stored history. There is no durable, cross-process session store.

CLI and FastAPI default Memory inherit `RuntimeConfig.memory_token_budget`, including
`MEDAGENT_MEMORY_TOKEN_BUDGET` overrides. FastAPI creates one Coordinator at module
initialization and retains its session-isolated Memory across requests; restart the
process after changing environment configuration. An explicitly injected `SessionMemory`
keeps its own configuration instead of inheriting RuntimeConfig's budget.
The `memory_read` event reports the effective budget, not merely the environment setting.
The checked-in `.env.example` does not yet enumerate the Memory budget or infrastructure
retry setting; the code and table above are authoritative.

## Clinical RAG configuration

Offline-safe modes require no optional dependency:

```text
MEDAGENT_RETRIEVAL_MODE=off   # retrieval calls fail explicitly
MEDAGENT_RETRIEVAL_MODE=fake  # deterministic tests/fixtures
```

The config-created `FakeRetrievalBackend` is empty; tests may inject synthetic items.
It is not a clinical knowledge base, and a real LLM with fake retrieval is still an API
call. Backend mode does not force the model to invoke a retrieval tool.

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

RAG queries use a separate immutable per-call context: current Subtask ID/description,
all assigned deliverables and RequestItems, plus model tool input. No runtime metadata
is added to the public tool arguments. Explicit unassigned request spans are removed,
shared question context is retained, and unsplit questions are retained once. Trace
records the exact backend query and task mapping. Admission is score-threshold based,
not a medical relevance, freshness, or provenance guarantee; there is no Reranker here.

`clinical_guideline`, `disease_code`, `recommend_lifestyle`, `deep_research`, and
`search_knowledge` are names for wrappers around the configured retrieval backend, not
independent clinical APIs or validated clinical algorithms. `analyze_symptoms` and
`assess_risk` are illustrative notice-returning utilities, not diagnostic/risk engines.
The schema catalog also contains `search_history`, but the native registry does not
register or expose it. The opt-in external MedicalQA loader is documented in
[its historical integration report](docs/MEDICALQA_NATIVE_RAG_INTEGRATION.md); it does
not distribute a database or establish current clinical efficacy.

## API

```bash
curl http://127.0.0.1:8000/health
curl -X POST http://127.0.0.1:8000/api/analyze \
  -H "Content-Type: application/json" \
  -d '{"description":"Synthetic example: intermittent fatigue","question":"What should be assessed?","session_id":"demo"}'
```

The response contains `final_answer`, `run_id`, `trace`, and `presentation`.
`presentation.professional_answer` is always identical to `final_answer`.
Use `status`, `user_request_complete`, and `final_request_item_answers` to inspect final
structural completion. `request_item_answers` is Worker-stage coverage, not necessarily
the displayed final answer. An incomplete run may intentionally have an empty final body.

## Frontend and trace replay

| Path | What actually executes | What it does not prove |
| --- | --- | --- |
| Default offline CLI/API | Native engine with `DeterministicLLM` | Learned reasoning or real model inference |
| Configured live CLI/API | Real OpenAI-compatible endpoint; retrieval selected separately | Medical effectiveness or real RAG unless actually configured/invoked |
| Demo Cases selection | Local synthetic fixture + presentation adapter | A runtime/model/database execution |
| Replay | Read an existing Trace's saved final output | A fresh run or the authenticity of its original provider |

```bash
cd web
npm ci
npm run dev
# Or create static build output:
npm run build
```

Patient, Clinical, and Developer views are provided. Evidence Cards contain only admitted
references and do not claim patient-specific proof. Large structured trace content is
collapsed by default in Developer View. Selecting **Demo Cases** loads checked-in
synthetic fixtures without calling the runtime, LLM, or retrieval; it is not a live run
or proof of dependency execution. **Analyze** calls `/api/analyze`; Vite's dev server
proxies that path to `127.0.0.1:8000`, so start `medagent serve` separately. A static build
needs separately configured API hosting/proxying for live Analyze.

Every run writes `runs/<run_id>/trace.jsonl` by default. Replay reads the recorded final
answer without invoking a model:

```bash
medagent replay runs/<run_id>
medagent trace runs/<run_id>
```

Replay only reads saved output; it neither re-executes tools nor verifies that the
original run used a real provider. `MEDAGENT_RUNTIME_MODE=replay` is rejected for live
execution: use the replay command instead.

**Trace privacy:** original `llm_request`, `llm_response`, `worker_draft`, synthesis,
and other audit payloads may retain case text, retrieved content, and model-produced
reasoning. Credential-pattern redaction is not general patient-data de-identification.
Handoff payloads and final-answer paths filter complete/unterminated `think`/`analysis`
blocks, but do not semantically recognize unmarked reasoning. Do not publish raw traces
or expose Developer View/run endpoints to untrusted users. Access control, retention,
consent, corpus governance, and deployment hardening are operator responsibilities.

## Validation

```bash
ruff check medagent api tests
pytest -q
# Narrow deterministic C1 reproduction (no provider required):
pytest -q tests/test_multi_agent_handoff_audit.py
```

The public tests need no model endpoint, Milvus service, or long-term-memory service.
`tests/test_runtime_claims.py` prevents the documented Memory, Skills, RAG, and full Trace
capabilities from becoming disconnected files or dead code.
Tests assert control flow, content boundaries, and coverage, not clinical effectiveness.
For Windows temporary-directory ACL failures, use a **new, unused** `--basetemp` directory
under a local writable scratch path; pytest owns and may clear that selected directory.
Do not point it at an existing data directory. CI also runs `ruff check .`,
`pytest -m "not integration"`, and the frontend build; these commands are not evidence
that every check or a real-provider evaluation has been rerun for a documentation change.

## Evaluation and license status

Historical design-baseline metrics in `docs/evaluation.md` are not measurements of
current main. Old reports retain their original scope and are not new clinical results.
No score improvement can be attributed to A1-C1 from unit-test results. Rerun readiness is documented in
`docs/NATIVE_BENCHMARK_READINESS.md`; this release does not rerun or alter the benchmark.

License selection and ownership confirmation remain user decisions. No legal-clearance
claim is made. See `LICENSE_PENDING.md` and `docs/OPEN_SOURCE_STATUS.md`.
