# MedAgent Harness

> **Version boundary — documentation-only review.** This branch is based on release `6147196` and adds only five documentation/SVG files. The W1–W7 implementation described here belongs to the unpublished local development snapshot `499d2fa`; it is **not included in this branch**. Shared source paths here still resolve to older release code and do not verify the development claims. The existing static Demo is unchanged.

MedAgent Harness is an Agent runtime for compound clinical questions. It turns a request into explicit requirements, schedules bounded work, and checks what was actually delivered. The engineering goal is to make task ownership, dependencies, evidence use, and failure states inspectable.

**The main contribution is the Harness control layer:** model proposals pass through program-controlled scope, permissions, budgets, and completion gates. Clinical questioning supplies a demanding workflow for this engineering portfolio; the project is not a medical device, does not replace qualified clinical judgment, and has not validated real clinical deployment, patient safety, or medical effectiveness.

[Static Demo](https://young-svg.github.io/medagent-harness/) · [Runtime design](docs/RUNTIME_ARCHITECTURE.md) · [W7 experiment results](docs/W7_REAL_MODEL_RESULTS.md)

## How a request runs

![MedAgent Harness: verified request specification, Planner and deterministic Policy, dependency-ready Workers and structured Handoff, then conditional synthesis, safety edits, structural, evidence and semantic acceptance. Memory, Tools/RAG, bounded recovery and causal Trace support execution.](docs/assets/medagent-architecture.svg)

[Open the full-size SVG](docs/assets/medagent-architecture.svg). Solid arrows show execution; dashed arrows show context support. The diagram describes the normal runtime, not the W7 evaluation adapters.

1. **Establish scope.** W1 uses two separate model contexts to extract and independently review source-aligned requirements. Program checks validate JSON, source spans, priorities and restrictions; `RequestSpec` assigns stable RQ IDs and `AnswerContract` defines deliverables. Uncertain or invalid specifications stop before planning.
2. **Constrain the plan.** The Planner proposes subtasks and dependencies. Deterministic Contract Policy reconciles required ownership; DAG and task-count validation reject invalid plans before dispatch. The Router reports the resulting single/multi route.
3. **Execute ready work.** Independent Workers run concurrently; dependent Workers wait for usable structured Handoff. Waiting does not occupy a Worker slot. Failed or timed-out upstream results block descendants without calling their models.
4. **Control model and tool loops.** Each Worker receives assigned RQ IDs, bounded history and a fixed role specification. Program checks enforce role/task tool permissions, argument schemas, budgets and duplicate tool-call ID rejection.
5. **Verify evidence when required.** Explicit external-source requests require actual retrieval, admission, delivery and authorized citation for the target RQ. Handoff preserves source lineage and permits only valid reuse. A citation's existence does not prove relevance, entailment or medical correctness.
6. **Accept the final result.** Shared `FinalAcceptance` checks Worker coverage before conditional Synthesis, then sanitization/safety edits, final structural coverage, Evidence Gate and W2 per-RQ semantic acceptance. A single successful Worker bypasses Synthesis. Unresolved hard blocks cannot return `completed`; optional omissions do not become required work.

W1 review and W2 use separate inputs, but may use the same underlying model. Agreement and valid answer quotations do not guarantee independent judgment or clinical correctness. Final states distinguish missing answers, off-topic content, insufficient information, uncertainty and execution errors.

## Development engineering decisions

| Boundary | Implemented control |
| --- | --- |
| Execution | Validated DAG; structured Handoff; default 16-subtask ceiling and 4 concurrent Workers |
| Budgets and recovery | Default 8 actual model attempts and 2 tool attempts **per subtask**; bounded infrastructure retry, length recovery and serialization repair |
| Tool side effects | Repeated tool-call IDs terminate that subtask before re-execution; this is not persistent cross-run idempotency |
| Evidence | Optional RAG; per-RQ retrieval authorization, source-category checks and citation lineage |
| Context and sessions | One bounded history snapshot; fresh Session when omitted; process-local state; explicit IDs select history, not user identity |
| Security and Trace | Trusted local API boundary; optional operator Bearer secret; metadata Trace by default with validated causal/source IDs |
| Role specifications | Four fixed Diagnostic, Consultation, Research and Synthesis instructions; audited names/hashes, no dynamic task-Skill Router |

These native limits are not a global token or monetary cap. W7 additionally enforces run/batch request budgets. Natural-language injection detection is limited; tool permissions and scope remain program-controlled. See [Architecture](docs/ARCHITECTURE.md) and the [W1–W6 implementation records](#implementation-and-verification).

## What W7 actually showed

W7 has 24 frozen SYNTHETIC cases / 40 RQs (`experiments/w7/frozen_v1/README.md`; unpublished development file), a shared scoring rubric (`experiments/w7/frozen_v1/SCORING.md`; unpublished development file), and four evaluation adapters (`eval/w7/README.md`; unpublished development file):

- **A — Single-Agent ReAct:** one AgentLoop, no Planner.
- **B — Single-Agent Plan-and-Execute:** real Planner/Policy, one general execution role in topological order, structured upstream results.
- **C — Native Planner-DAG:** existing role assignment, DAG, Handoff and conditional Synthesis.
- **C-serial:** replays C's exact Policy-finalized plan with Worker concurrency 1; no new planning.

The real runs below use the same frozen RequestSpec, Contract and evidence requirements, **skip W1 and retain W2**, use common tool capabilities and disable extra Presentation LLM calls. Per run, the evaluation ceilings are 8 Worker requests, 8 tool attempts and 16 total model requests. Request counts include Planner, Workers, W2 and Synthesis. These are execution-layer comparisons, not end-to-end W1 quality experiments.

### Real A/B/C pilot — 2026-11-04

Configured model alias: `deepseek-flash`. Responses reported `deepseek-v4-1-flash-261017` through an OpenAI-compatible gateway; backend identity was not independently verified. Tokens are Provider-reported input/output usage.

| Case | Architecture | Model requests | Input / output tokens | End-to-end seconds |
| --- | --- | ---: | ---: | ---: |
| S02: supplied reference interval | A | 2 | 1,861 / 679 | 10.128 |
| S02 | B | 3 | 2,677 / 1,035 | 16.819 |
| S02 | C | 3 | 2,633 / 950 | 15.057 |
| I03: independent requirements | A | 2 | 2,240 / 1,456 | 18.835 |
| I03 | B | 4 | 4,229 / 3,472 | 37.207 |
| I03 | C | 5 | 6,114 / 2,696 | 26.762 |
| D03: dependent requirements | A | 2 | 3,031 / 3,413 | 32.798 |
| D03 | B | 5 | 7,513 / 4,673 | 51.275 |
| D03 | C | 6 | 10,962 / 5,788 | 54.283 |

All nine reached internal `completed`; **independent quality is UNSCORED for every run**. A used fewer requests and less elapsed time in these observations. There is no evidence here that B or C improved answer quality.

### Same-plan concurrency and Memory — 2026-11-05

C and C-serial share identical complete frozen plans and condition hashes. Both reached internal `completed`; observed Worker peaks were 2 and 1 respectively.

| Case | Worker request span C / serial (s) | Actual end-to-end C / serial (s) | Shared planning (s) |
| --- | ---: | ---: | ---: |
| I03 | 9.150 / 15.122 | 30.727 / 26.930 | 5.988 |
| D03 | 31.480 / 51.096 | 67.019 / 87.864 | 7.537 |

The Worker span runs from the first Worker model request to the last response, not exact scheduler occupancy. C's actual time includes planning; serial's excludes it. Planning cost is attributed separately, not charged twice. Concurrency shortened the observed Worker spans, but **I03 did not show an end-to-end benefit including planning**. D03 showed real fan-in waiting and two structured Handoffs with no observed dependency-order violation.

M01 A received the identical current request with Memory On/Off. On injected one historical message and returned the supplied two symptoms; Off injected zero, requested missing history and returned internal `incomplete`. Costs were 2 requests each, 1,773/279 versus 1,775/545 input/output tokens, and 8.850 versus 9.804 seconds. Human quality remains UNSCORED; safe insufficiency is not a Provider failure.

**Architecture choice:** keep the single-loop path available when explicit orchestration adds little; use DAG/Handoff when dependency ownership and traceability are needed. Parallelism is useful for independent work, but planning, synthesis, output length and network variation can offset latency gains. These results do not establish that Multi-Agent generally outperforms ReAct.

Each condition ran once. Cache effects, fixed execution order, output length, prompts, role assignment and synthesis differ across architectures. No statistical or clinical conclusion follows. The real case runs did not invoke tools; a separate two-request protocol probe exercised one read-only SYNTHETIC retrieval and continuation. E03 source isolation, repeated tool-call rejection and injected recovery/failure blocking have **offline mechanism evidence**, not full real-model retrieval-quality validation. D05 remains BLOCKED in the current Runner. Historical tool-continuation **HTTP400 remains UNKNOWN and unresolved**.

[Detailed results, cost attribution and evidence limits](docs/W7_REAL_MODEL_RESULTS.md) · [HTTP400 record](docs/HTTP400_KNOWN_ISSUE.md)

## Try the static Demo

The [GitHub Pages showcase](https://young-svg.github.io/medagent-harness/) presents Single, Multi/Handoff, RAG and Memory scenarios using **precomputed SYNTHETIC fixtures**. It makes no live model or database calls and requires no API keys.

```bash
cd web
npm ci
npm run dev
# Static build:
npm run build
```

See the [Demo guide](docs/STATIC_DEMO.md). The live runtime is a separate CLI/API; the public showcase is not a medical service.

## Development setup reference

The settings and validation commands in this section describe the unpublished development tree, not the code shipped in this documentation-only branch. For executable instructions matching this checkout, use the [released README](https://github.com/young-svg/medagent-harness/blob/61471969e187b665e37ee7227bf987c93e6a7bf6/README.md). Do not expect W1/W2 or `eval/w7` interfaces to exist in release `6147196`.

Requires Python 3.11+.

```bash
python -m venv .venv
# Activate the environment, then:
pip install -e ".[dev]"
medagent --help
```

Without an endpoint, the client is `DeterministicLLM`, not learned medical inference. W1/W2 default to enabled; the local fake cannot certify semantic validation, so default offline analysis may return `incomplete`. Use the static Demo or ScriptedLLM tests for deterministic workflow examples. Explicit `MEDAGENT_SEMANTIC_VALIDATION=false` selects structural-only testing, never a semantic-validation success.

For a real endpoint, explicitly export these placeholders in the process environment; CLI/API do not auto-load `.env`:

```text
MEDAGENT_LLM_BASE_URL=https://your-endpoint.example/v1
MEDAGENT_LLM_API_KEY=replace-me
MEDAGENT_LLM_MODEL=your-model
```

`medagent run examples/synthetic_case_1.json` uses the configured runtime. Paid calls, default W1/W2 verification, bounded recovery and optional Presentation conversion can add requests. W7's live adapters disable automatic recovery and extra Presentation calls; their request costs are not default CLI costs.

RAG is separate: `MEDAGENT_RETRIEVAL_MODE=off|fake|milvus`. Fake starts empty unless a fixture is injected. Optional Milvus dependencies are installed with `pip install -e ".[retrieval]"`; a lawful user-managed corpus and explicit configuration are required. Tool names such as `clinical_guideline` and `disease_code` wrap retrieval, not validated clinical services. See [RuntimeConfig](medagent/runtime/config.py) for stage output ceilings, timeouts and backend settings; output ceilings are not global token limits.

### Local API, sessions and Trace

`medagent serve --host 127.0.0.1` supports a **trusted local operator**, not a secure public multi-user service. The API checks loopback peer/Host and browser-origin constraints. If `MEDAGENT_API_TOKEN` is configured, protected endpoints also require `Authorization: Bearer <token>`. A shared secret does not provide user accounts or tenant authorization.

Omit `session_id` to get a fresh Session; explicitly reuse the returned ID for multi-turn history. Session IDs are selectors, not credentials. Run lookup additionally requires the corresponding Session and same operator context. See W3 boundaries (`docs/W3_SESSION_SECURITY.md`; unpublished development file).

Trace defaults to `MEDAGENT_TRACE_CONTENT=metadata`: validated event/source IDs, stages, status, usage and timing, with Prompt, history, answer and exception bodies omitted. `medagent trace runs/<run_id>` and `medagent replay runs/<run_id>` read local records. Metadata Replay cannot restore omitted answers; explicit `raw` can, but raw Trace may contain sensitive text and must not be published. Neither mode certifies medical de-identification.

## Implementation and verification

| Area | Source / engineering record |
| --- | --- |
| W1 / W2: requirement and final semantic gates | Semantic validation (`medagent/runtime/semantic_validation.py`; unpublished development file), shared acceptance (`medagent/runtime/acceptance.py`; unpublished development file), record (`docs/W1_W2_CONTROLLED_FIX.md`; unpublished development file) |
| W3: sessions, local access, untrusted context, privacy | [API](api/main.py), privacy projection (`medagent/observability/privacy.py`; unpublished development file), record (`docs/W3_SESSION_SECURITY.md`; unpublished development file) |
| W4: attempts, scheduling, recovery, tool replay protection | [AgentLoop](medagent/runtime/agent_loop.py), record (`docs/W4_RUNTIME_BUDGETS.md`; unpublished development file) |
| W5: RQ-scoped evidence and citations | [Coverage](medagent/runtime/coverage.py), record (`docs/W5_RAG_EVIDENCE_CLOSURE.md`; unpublished development file) |
| W6: fixed role specifications and audit | [Specs](medagent/skills/specs/), record (`docs/W6_SKILL_CAPABILITIES.md`; unpublished development file) |
| W7: frozen inputs, plan replay, drift guards, fair budgets | Interfaces (`medagent/runtime/evaluation_inputs.py`; unpublished development file), Runner (`eval/w7/runner.py`; unpublished development file), [results](docs/W7_REAL_MODEL_RESULTS.md) |

For the unpublished development snapshot, the latest recorded full offline regression was: **986 passed**, including 13 live-adapter tests using mocked HTTP; this is engineering evidence, not 986 real-model trials. Earlier W7 offline Smoke recorded 56 ScriptedLLM runs across five cases and fault variants. No model batch or full regression was rerun for this documentation update.

```bash
python -m pytest -q
ruff check medagent api tests eval/w7
git diff --check
```

The commands above require the development snapshot; `eval/w7` is absent from this branch. Existing release CI remains unchanged and offline. For Windows temporary-directory ACL issues, use a fresh repository-external `--basetemp`, without changing historical directory permissions.

Earlier [A1 real-LLM integration](docs/REAL_LLM_A1_VALIDATION.md) and [A2 synthetic Milvus Lite integration](docs/REAL_MILVUS_A2_VALIDATION.md) retain their original limited scope. The [historical 60-case comparison](docs/BENCHMARK.md) belongs to old version `80c98fe`, not W7 or current performance. Archived exploratory A3 results are not part of this project's current headline evidence.

## Data, authorization and license

The new public W7 summary contains aggregate statistics, not raw Trace, prompts or response dumps. Frozen W7 cases and retrieval sources are explicitly SYNTHETIC; they are not real clinical guidelines or papers. Raw local experiment artifacts, credentials, databases and model caches must stay outside source submissions.

These declarations are not a provenance, medical privacy or redistribution certification. Reachable Git ancestors retain older materials; a clean current tree is not a sanitized history. Publication authority for source, prompts, demo text, prior excerpts and third-party assets remains subject to owner confirmation. See the [Public release audit](docs/PUBLIC_RELEASE_AUDIT.md); this documentation pass does not replace its outstanding authorization decisions.

This repository is provided for public viewing and evaluation. **No open-source license is granted at this time.** Third-party components remain subject to their respective licenses.
