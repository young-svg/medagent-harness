# Design decisions

## Why centralized Planner–Worker

Clinical-domain work benefits from one auditable place for decomposition,
dispatchability and fallback. Workers specialize; they do not negotiate an
unbounded decentralized swarm.

## Why not a LangGraph migration

Freeze v2 already established the runtime behavior. A graph migration would be
a new architecture and could change timing, retry and state semantics without
improving the domain policy being demonstrated. LangGraph remains a reasonable
generic runtime option.

## Why session memory instead of default long-term memory

Recent conversational context is necessary and bounded. Personalized vector
memory creates consent, retention, deletion and cross-session leakage risks.
Long-term memory is therefore an unconfigured protocol only.

## Why RAG admission

Retrieval is not evidence acceptance. A unified evidence structure keeps raw
ranked results in the trace, applies an explicit threshold/cap, and sends only
compact admitted items to workers.

## Why guardrail editing is conservative

Detection is not automatic edit permission. Rewriting a mixed semantic unit can
remove correct content or introduce a new claim. Exact Stable-ID units may be
patched; ambiguity preserves the original answer and remains visible in trace.

## Why trace/replay is first-class

Agent quality cannot be debugged from a final string. Trace records context,
plan, route, tools, evidence, drafts, checker actions and operational metrics.
Replay inspects prior outputs without another model call.

## Why generic runtime versus domain harness policy

Scheduling, persistence and transport are generic-runtime concerns. Contract
shape, clinical retrieval routing, evidence admission, validation and
evaluation are domain harness policies and can move to another runtime.

## Why not DeepSeek Harness?

DeepSeek Harness, LangGraph and similar systems may be suitable generic
runtimes. This project is not a comparison or criticism. Its focus is the
portable clinical-domain policy around context, planning, retrieval, evidence,
validation and evaluation. Replacing the runtime is intentionally outside v1.

