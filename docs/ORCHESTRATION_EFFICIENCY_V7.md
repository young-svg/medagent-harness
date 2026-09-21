# Orchestration Efficiency v7

## Scope

This change controls orchestration breadth without changing the answer model, model settings,
generation recovery, stage budgets, retrieval threshold, evidence admission, memory, guardrails,
or the clinical corpus.

## Deterministic complexity and response profiles

`TaskComplexityProfile` is derived after `AnswerContract` construction from requested
deliverables and explicit wording in the current question. It records breadth, deliverable
count, diagnosis/differential/management needs, external-evidence need, cross-role need, and
whether a multi-agent plan is justified. It does not call an LLM.

`ResponseProfile` maps focused, moderate, and comprehensive complexity to focused, standard,
and comprehensive response objectives. Profiles never impose a character cap. Focused answers
still retain essential rationale and safety advice.

## Contract-aware planning and routing

The planner is asked to attach every subtask to one or more requested deliverable IDs and to
provide a short justification. A deterministic post-planner policy removes unmapped work,
unneeded research expansion, and duplicate or same-role worker assignments. Clearly focused
plans are collapsed to the best retained contract-mapped subtask. Moderate and comprehensive
plans retain distinct necessary work, so efficiency policy does not force comprehensive tasks
to a single worker.

Routing remains a consequence of the validated dispatchable plan. One retained subtask routes
single; multiple necessary subtasks route multi. A single successful worker still bypasses
synthesis.

## Capability and retrieval gating

Tool visibility is the intersection of the worker role allowlist and a per-request capability
filter. The same filter is enforced again during tool execution. Symptom and risk utilities can
remain available to appropriate clinical roles, but guideline, classification, research, and
knowledge retrieval tools are exposed only when the current question explicitly requires
external evidence. The external-evidence kind further limits guideline, research, and
classification schemas to the requested capability. A medical topic alone does not enable
retrieval.

## Contract-first synthesis

Synthesis receives the contract, complexity profile, response profile, and worker drafts. Its
objective is the smallest complete answer that satisfies requested deliverables and must-cover
items. It removes repetition and unrelated worker expansion, does not introduce new
deliverables, and preserves medically necessary rationale and safety warnings.

## Leakage boundary

Production policy uses only the current question, `AnswerContract`, evidence ledger, planner
output, and deterministic public rules. It has no benchmark IDs, reference answers, comparison
answers, manual scores, or case-specific branches.
