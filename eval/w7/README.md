# W7 architecture evaluation

This candidate includes the evaluated implementation: frozen manual inputs, A/B/C/C-serial
adapters, run/batch request budgets, safe metering and opt-in live Provider integration.
The original experiments and their limits are summarized in
[W7 results](../../docs/W7_REAL_MODEL_RESULTS.md). This directory contains code and fixed
instructions, not raw experiment outputs or answer dumps.

The public checkout omits two internally archived design originals while preserving
all runtime payload hashes. Use [public packaging checks](../../experiments/w7/public_release/README.md);
the immutable full-archive validator requires the internal originals and is not a
public-checkout validation command.

## Offline execution

From the repository root, choose a **new** output directory:

```powershell
python -m eval.w7.smoke --output <new-local-directory>
```

Smoke is ScriptedLLM-only: five cases (S02, I03, D03, E03, M01), four architectures,
Memory Off and fault/budget variants, 56 runs in total. No Provider or Milvus is called.
Responses and validation judgments are scripted, not quality results. Missing usage
stays UNKNOWN. Outputs include per-run JSON, metadata Trace, a matrix and SHA manifest;
keep them out of source submissions.

| Group | Actual path |
| --- | --- |
| A | One full-task AgentLoop, no Planner; shared FinalAcceptance |
| B | Real Planner/Policy, one general execution role sequentially in topological order, structured Handoff; shared FinalAcceptance |
| C | NativeEngine analyze_frozen, post-Policy DAG, role Workers, Handoff and conditional Synthesis |
| C-serial | NativeEngine replays C's full FrozenExecutionPlan with concurrency 1; no Planner or Policy regeneration |

A/B use the fixed general_executor.md instructions and existing diagnostic permission
identity, not specialist prompt substitution. Common task tools are intersected with
existing role permissions: E03 has disease_code/search_knowledge; other supported cases
have an empty tool envelope. This does not imply support for all 24 frozen cases;
D05 remains BLOCKED. No permission is expanded to make a comparison pass.

## Reproducibility and measurement

The adapters use the same frozen RequestSpec, Contract, restrictions and per-RQ evidence
requirements from frozen_v1. They skip requirement extraction but retain semantic
acceptance, Guardrail hard blocks, structural coverage and Evidence Gate. RAG uses the
frozen SYNTHETIC corpus and topic/Collection selector. Session history is seeded into a
fresh instance; Memory Off suppresses reads and writes. C/C-serial bind the same plan,
request and execution conditions. Extra Presentation LLM calls are disabled.

RunBudget reserves before model/tool attempts under an asyncio lock: 8 Worker requests,
8 tool attempts and 16 total model requests per run. Native per-subtask limits remain.
Retrieval tool attempts and actual Handler entries are counted separately. Retries,
length recovery and protocol repair pass through the same meter. Once exhausted,
the budget stops subsequent attempts; in-flight work cannot be revoked.

Records distinguish actual requests from pre-call Trace attempts, Provider usage from
scripted simulation, known token subtotals from UNKNOWN usage, and internal completion
from independent human scoring. Failed requests are not free zero-token requests.
C-serial has zero actual planning calls; the shared plan-generation cost is separately
attributed for full-architecture comparison and not charged twice to actual usage.

Conditions guard local model parameters, actual Memory configuration/history, tool
schemas and Fake corpus content. They do not freeze remote Provider or Milvus state.
The historical runner_source_state=uncommitted label is static; use code_commit and
runner_sources_sha256 for actual provenance, as explained in the results report.

## Opt-in live mode

Only with explicit paid-call authorization and the existing Provider configuration:

```powershell
python -m eval.w7.live --mode live --output <new-local-directory>
```

This command is **not** a dry-run. It sends bounded P1/P2 protocol probes (3 requests
maximum), then only if both pass runs S02/I03/D03 times A/B/C (144 batch requests maximum,
16 per run). It reads MEDAGENT_LLM_BASE_URL, MEDAGENT_LLM_API_KEY and MEDAGENT_LLM_MODEL
from process environment; it does not auto-load .env or fall back to scripted mode.

Live mode uses OpenAICompatibleLLM, disables automatic retries/length recovery/protocol
repair and extra Presentation calls, and stops on HTTP, tool, protocol or budget errors.
Usage is provider_reported or UNKNOWN, not estimated. Secrets are not printed. Local
outputs contain actual answers and must be handled separately from public source.
No live command is run as part of candidate release validation.

Formal independent quality remains UNSCORED until rubric-based human review is supplied.
HTTP400's historical root cause is still UNKNOWN and unresolved; current successful
protocol probes do not diagnose or repair it. No real retrieval-quality or clinical
effectiveness claim follows from these adapters.
