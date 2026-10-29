# MedAgent Harness

MedAgent Harness is a Planner–Worker runtime for complex clinical questions that combine assessment, investigation, management, and follow-up. It makes task ownership, evidence use, and failure handling inspectable instead of relying on one long model response to cover every requirement.

The project focuses on execution engineering, not training a new foundation model: dependency-aware scheduling, structured handoff, bounded tools and context, final-answer coverage, and causal traces.

[Explore the static Demo](https://young-svg.github.io/medagent-harness/) · [Runtime design](docs/RUNTIME_ARCHITECTURE.md) · [Real LLM validation](docs/REAL_LLM_A1_VALIDATION.md) · [Synthetic Milvus validation](docs/REAL_MILVUS_A2_VALIDATION.md)

Built as an agent-engineering portfolio project, it is not a medical device, does not replace qualified clinical judgment, and does not guarantee a diagnosis. Real clinical deployment, patient safety, and medical effectiveness have not been validated by the engineering regression suite.

## Why MedAgent Harness?

One question may require several outputs, while a later task may need an earlier result. The engineering problem is to preserve those requirements through planning, tool use, synthesis, and safety edits, and to explain where execution stopped when a provider or worker fails.

**The core contribution is the Harness control layer:** it converts each user request into an explicit execution contract and controls the Agent lifecycle from planning and stage-aware context construction through worker dispatch, tool execution, verification, recovery, and tracing.

## Architecture

![MedAgent Harness Runtime Architecture: deterministic specification and policy around LLM planning, ready-task scheduling, parallel Workers or dependency Handoff, conditional synthesis and final structural verification; dashed links distinguish Session Memory from tools and external evidence.](docs/assets/medagent-architecture.svg)

[Open the full-size architecture](docs/assets/medagent-architecture.svg). Solid arrows show execution; dashed arrows show support/context. Handoff is dependency-only. Synthesis runs only after coverage passes and at least two Workers succeed; a single successful Worker bypasses it. Structural coverage is not semantic or clinical validation.

## How One Request Runs

**Requirements → task graph → Workers / Handoff → synthesis → safety edits → final structural coverage → answer and Trace.**

- `RequestSpec` preserves the user's explicit requirements and deliverables.
- `AnswerContract` defines the clinical dimensions the final answer must cover.
- The Planner proposes tasks and dependencies; contract policy may consolidate low-scope independent work. Declared dependency graphs are preserved and validated.
- Each worker receives role-relevant, stage-aware bounded context rather than one shared oversized prompt.
- Tools and optional RAG inject evidence only through the controlled worker loop; session memory remains bounded, process-local, and session-scoped.
- Coverage is checked again on the final per-item answers after synthesis and safety edits. Structural completion does not certify semantic or medical correctness.

See [Architecture](docs/ARCHITECTURE.md) for the detailed execution and failure model.

## Engineering Capabilities

| Capability | Implementation |
| --- | --- |
| Task ownership | Explicit request IDs, role-scoped context, and final per-item coverage |
| Dependencies | Acyclic `depends_on`, structured Handoff, upstream-failure blocking |
| Tools | Published argument-schema validation, worker permissions, shared call budgets |
| RAG | Subtask-aligned queries, optional Milvus backend, threshold admission and source tracing |
| Session Memory | Isolated, process-local history with message-count and estimated-token budgets |
| Reliability | Bounded transport retries and serialization recovery; causal Trace and sanitized HTTP diagnostics |

<details>
<summary>Runtime guarantees, boundaries, and implementation / regression-test links</summary>

The A1–C1 labels in the implementation table below refer to earlier engineering fixes, not the V1 real-integration validations summarized under Engineering Validation.

## Runtime Correctness and Dependency Execution

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
| Estimated Memory budget (B2) | [session memory](medagent/memory/session.py), [config](medagent/runtime/config.py) | [memory budget](tests/test_session_memory_budget_audit.py), [API budget](tests/test_api_memory_budget.py) |
| Dependency scheduling / Handoff (C1) | [planner](medagent/planning/planner.py), [handoff](medagent/runtime/handoff.py) | [dependency and sanitization tests](tests/test_multi_agent_handoff_audit.py) |

For a compact dependency-plan example and operating limits, see
[architecture](docs/RUNTIME_ARCHITECTURE.md) and the [behavior specification](docs/NATIVE_IMPLEMENTATION_SPEC.md).

</details>

## Key Reliability Design

Two bounded worker recovery paths are emphasized:

1. **Infrastructure retry** handles transient provider or network failures such as transport errors, rate limits, and server errors with at most two retries by default and exponential backoff (configurable from zero to three).
2. **Worker protocol recovery** handles non-empty semantic content that fails the required structured-output protocol.

Protocol recovery is limited to one serialization-repair attempt. It does not rerun the Planner, replay tools, or trigger a quality retry. Its output must pass the same parser, request-coverage gate, and Answer Contract gate as the original worker response. Stage-specific generation-length handling is also bounded and is documented in [Architecture](docs/ARCHITECTURE.md#8-reliability-control).

This mechanism was introduced after failure analysis of an early 20-case run: two incomplete cases contained clinically relevant worker content but failed at the structured-response boundary. The root cause was malformed serialization and parser rejection—not medical reasoning, planning, or network failure—so the fix targeted bounded serialization recovery without rerunning planning, tools, or clinical reasoning. The final fixed 60-case valid-execution set completed 60/60; this is not a claim of perfect reliability. See [Failure analysis](docs/FAILURE_ANALYSIS.md) for details.

## Engineering Validation

### Current V1 engineering evidence

The validation focus is the Harness control layer: task ownership, dependency execution, tool/evidence boundaries, session isolation and bounded recovery. Offline regressions and real-provider integration are separate evidence sets, not a pooled clinical benchmark.

| Validation | Observed result | Evidence and limits |
| --- | --- | --- |
| Offline engineering regression | **459 tests passed** at the V1 release validation | Covers tools, coverage, dependencies/Handoff, RAG, Memory, recovery and Trace; not clinical effectiveness |
| A1: real LLM integration | Single Worker, independent Multi-Agent, tools, real LLM + Fake Retrieval, and three-round Memory passed their scoped checks; dependent Handoff **PARTIAL** | [A1 report](docs/REAL_LLM_A1_VALIDATION.md): 33 real requests; dependent execution worked but RequestSpec retained only one RQ1 |
| A2: real LLM + synthetic Milvus Lite | End-to-end **PASS** in one synthetic case; four offline retrieval scenarios passed after two targeted fixes | [A2 report](docs/REAL_MILVUS_A2_VALIDATION.md): 512-dimensional normalized embeddings, COSINE, threshold 0.63; not a real medical knowledge-base evaluation |

Implementation and offline regression evidence:

- **Dependencies / Handoff:** [validated public-result transfer](medagent/runtime/handoff.py) and [concurrency, provenance and failure-blocking tests](tests/test_multi_agent_handoff_audit.py).
- **RAG:** [subtask-aligned query construction](medagent/retrieval/query_builder.py) and [query-alignment regressions](tests/test_retrieval_query_alignment_audit.py).
- **Session Memory:** [bounded session history](medagent/memory/session.py) and [budget, shared injection and isolation tests](tests/test_session_memory_budget_audit.py).
- **Recovery:** [Worker recovery flow](medagent/runtime/agent_loop.py) and [serialization-only recovery tests](tests/test_worker_protocol_recovery.py).

System completion and structural coverage are not task success or answer accuracy. The real-integration cases and retrieval documents above are synthetic; raw traces and internal exploratory evaluations are not published here. Offline fault injection is not evidence of real-provider recovery behavior.

Known limitation: RAG tool-continuation HTTP400 remains **unresolved; root cause UNKNOWN**. See the [diagnostic record](docs/HTTP400_KNOWN_ISSUE.md).

### Historical 60-case quality comparison — old version

| Fixed 60-case development evaluation | Result |
| --- | ---: |
| Reliability | **60 / 60 completed** |
| MedAgent clinical decision-support quality | **4.6733 / 5** |
| DeepSeek Web saved-output baseline | **4.3883 / 5** |
| Delta | **+0.2850 / +6.49%** |

The largest gains were in:

- Completeness: **+0.7166**
- Workflow: **+0.7000**

MedAgent and the DeepSeek Web baseline used the same underlying DeepSeek model. This is a paired complete-system comparison of different orchestration, context-management, and execution pipelines rather than foundation-model scale or family. Because DeepSeek Web's internal configuration is not observable, this is not a strict component-level causal ablation, and the **+6.49%** delta is not attributed solely to the Harness.

These scores belong to the release evaluation before A1-C1 integration (release baseline `80c98fe`). They are not scores for the current V1 runtime. A1/A2 provide the limited real-provider and synthetic-Milvus evidence above; neither reran this clinical-quality comparison or establishes a +6.49% improvement for the current version.

Runs followed a frozen validity policy: valid model responses were never regenerated for answer quality or manually edited; detailed methodology is documented in [Benchmark](docs/BENCHMARK.md) and [Reliability](docs/RELIABILITY.md).

This comparison measures clinical decision-support quality on a fixed 60-case development evaluation set. It is not an unseen test, independent clinical validation, or evidence of general medical intelligence. Retrieval is optional and is not claimed as the primary source of the reported improvement.

## Demo

[MedAgent Showcase Demo](https://young-svg.github.io/medagent-harness/)

The frontend presents four bundled, **precomputed static** scenarios: single-agent routing, dependency-aware Multi-Agent Handoff, synthetic RAG evidence, and same-session memory.

- Fixture-only; no backend required.
- No API keys.
- No external inference calls.
- The Multi fixture illustrates ST1 → validated public Handoff → ST2; it is not a recorded live execution or evidence that every Planner chooses that route.
- Engineering portfolio showcase, not an online medical service.

### Run the showcase locally

```bash
cd web
npm ci
npm run dev
```

See the [Static demo](docs/STATIC_DEMO.md), [Static demo audit](docs/STATIC_DEMO_AUDIT.md), and [Demo guide](docs/DEMO.md).

## Quick Start

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

A real-provider run may also perform the existing optional presentation transform after a
complete final answer. This is a separate expression-only model call; offline clients do
not enable it unless explicitly injected. Incomplete answers skip the transform. Existing
bounded Worker protocol recovery and infrastructure retries can add provider attempts;
structured synthesis recovery and Handoff validation do not add repair calls.

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
| `MEDAGENT_WORKER_MAX_INFRA_RETRIES` | `2` | `0` to `3` transient infrastructure retries per Worker generation |
| `MEDAGENT_WORKER_INFRA_RETRY_BASE_DELAY_SECONDS` | `1` | Backoff base delay in seconds, bounded to `0` to `30` |
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
The checked-in `.env.example` includes the release retry controls but does not yet
enumerate the Memory budget; the code and table above are authoritative.

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
register or expose it. External corpus loaders do not distribute a database or establish
clinical efficacy; operators must supply and govern any real corpus themselves.

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
or proof of dependency execution. The release frontend is a fixture-only static showcase; its public Analyze action does
not invoke runtime inference. Live inference is available through the separate CLI/API.
The GitHub Pages base path and deployment workflow are retained.

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

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Runtime details and offline dependency example](docs/RUNTIME_ARCHITECTURE.md)
- [Runtime behavior specification](docs/NATIVE_IMPLEMENTATION_SPEC.md)
- [RequestSpec and final coverage](docs/REQUESTSPEC_ARCHITECTURE.md)
- [Design](docs/DESIGN.md)
- [Benchmark](docs/BENCHMARK.md)
- [Reliability](docs/RELIABILITY.md)
- [Failure analysis](docs/FAILURE_ANALYSIS.md)
- [Real LLM validation (V1 A1)](docs/REAL_LLM_A1_VALIDATION.md)
- [Synthetic Milvus validation (V1 A2)](docs/REAL_MILVUS_A2_VALIDATION.md)
- [Demo guide](docs/DEMO.md)
- [Static demo](docs/STATIC_DEMO.md)
- [Static demo audit](docs/STATIC_DEMO_AUDIT.md)

## Repository Layout

```text
api/                 FastAPI boundary
docs/                Architecture, evaluation, reliability, and demo documentation
eval/                Reusable evaluation schemas and scoring utilities
examples/demo_cases/ Synthetic showcase fixtures
medagent/            Context, planning, runtime, agents, tools, retrieval, and verification
tests/               Deterministic unit and integration-contract tests
web/                 React/Vite frontend
```

## Validation

```bash
pytest -q
ruff check medagent api tests

cd web
npm ci
npm run build
```

CI runs offline and does not require a model endpoint, vector database, patient dataset, or private corpus. For Windows temporary-directory ACL failures, use a new, unused writable `--basetemp` outside the repository. Tests cover engineering behavior, not clinical effectiveness.

## Public-release Data Policy

The current release tree does not include patient records, benchmark case dumps,
private corpora, vector databases, model weights, credentials or raw local traces.
Demo inputs and evidence cards are labeled synthetic fixtures. These labels are
not an independent provenance or redistribution-rights audit.

Git history was not rewritten: ancestors retain older traces and evaluation
excerpts excluded from the current tree. Owner confirmation of publication rights
must cover that history as well. See the current scope and outstanding decisions
in [Public release audit](docs/PUBLIC_RELEASE_AUDIT.md).

## License

This repository is provided for public viewing and evaluation.

No open-source license is granted at this time.

Third-party components remain subject to their respective licenses.
