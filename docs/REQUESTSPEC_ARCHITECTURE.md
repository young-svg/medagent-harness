# RequestSpec-first Completion Architecture

## Purpose

`AnswerContract` previously represented both what the user explicitly asked and the
closed clinical capability taxonomy. That loses open-world requests whenever a question
has several explicit parts but only some parts map to known deliverables. A successful
known deliverable could therefore make `ContractComplete` true while other user questions
were unanswered.

The runtime now separates the two responsibilities:

```text
Original question
  -> RequestSpec (user-request completeness)
  -> AnswerContract (clinical semantic/capability mapping)
  -> Planner request_item_ids + deliverable_ids
  -> Worker per-item answers
  -> Request coverage + contract coverage
  -> synthesis/checker/final
```

## Data model

`RequestSpec.items` contains ordered `RequestItem` values:

- `id`: stable run-local ID such as `RQ1`.
- `text`: the requested item without its list marker.
- `required`: whether omission blocks completion.
- `order`: source order.
- `source_span`: exact source-backed text found in the original question.
- `semantic_type`: an existing AnswerContract deliverable ID or `UNKNOWN`.

Every extracted item is source-backed. The extractor never invents an item. If it cannot
identify reliable structural boundaries, it preserves the complete question as one required
item instead of guessing or dropping content.

## Deterministic extraction

The extractor recognizes two or more structurally explicit items:

- numeric markers: `1.`, `1)`, `1、`, `1）`, `(1)`, `（1）`;
- circled numbers: `①` through `⑳`;
- obvious bullet lines such as `-`, `*`, `+`, and `•`;
- multiple non-empty newline-separated lines when every line is visibly a question.

An `optional`, `可选`, or `选答` prefix marks an item optional. No LLM decomposer or extra
model call is used.

## UNKNOWN preservation

Semantic mapping reuses the existing AnswerContract taxonomy. An unrecognized item receives
`semantic_type=UNKNOWN`; it remains in RequestSpec and remains required. Benefits, etiology,
mechanism, and clinical-manifestation requests are not converted into a treatment-plan request
merely because their wording mentions a treatment or disease. This does not add any medical
taxonomy labels.

## Planning invariants

Planner input includes both RequestSpec and AnswerContract. Each `Subtask` carries both:

- `request_item_ids` for user-request coverage;
- `deliverable_ids` for clinical capability coverage.

The existing efficiency policy still merges work assigned to the same worker. Request count
does not imply worker count. After the planner response is parsed, deterministic policy code:

1. removes invalid request IDs;
2. gives every retained subtask at least one RequestItem;
3. attaches every missing required RequestItem to the closest existing worker by semantic type,
   deliverable mapping, and worker role;
4. uses the existing fallback when no planner work remains.

No second planning model call is made.

## Worker response

The final worker response is normalized to `WorkerResponse.answers`, represented at runtime by
`RequestItemAnswer(request_item_id, answer)`. For a worker assigned multiple request items, only
valid JSON entries whose IDs are assigned and whose answers are non-empty are accepted. Unknown,
duplicate, empty, and unassigned entries do not claim coverage.

For backward compatibility with existing single-item workers, a non-empty plain-text response
assigned to exactly one RequestItem is normalized into one validated `RequestItemAnswer`.
Multi-item plain text cannot claim multiple-item coverage. The tool loop, length recovery, and
infrastructure retry behavior are unchanged.

## Completion gates

Request coverage is the union of actual schema-valid non-empty per-item answers from successful
workers. Assignment alone never counts as coverage.

```text
UserRequestComplete = missing required RequestItems is empty
ContractComplete    = missing required clinical deliverables is empty
run completed       = UserRequestComplete AND ContractComplete
```

An optional RequestItem may be missing without blocking completion. Duplicate answers from
multiple workers are unioned by request item ID. A single worker that omits one of three assigned
items yields an incomplete run even if it returned other non-empty text.

Single-worker runs that satisfy both gates continue to bypass synthesis. Multi-worker synthesis
receives RequestSpec plus every per-item worker answer and is instructed not to omit required
items. The natural-language final answer remains Markdown-compatible.

## Result and trace

Runtime results retain `request_item_answers` and expose:

- `user_request_complete`;
- `missing_required_request_items`;
- `contract_complete`;
- existing `missing_required_deliverables`.

Trace additions are:

- `request_spec_built`, including item source spans and required flags;
- `plan_created.subtasks[].request_item_ids`;
- `worker_draft.answered_request_item_ids` and per-item answers;
- `request_coverage`, including required, covered, missing, and completion status.

`ContractComplete` and `UserRequestComplete` remain separate in trace summaries and reports.

## Scope preserved

This refactor does not expand medical taxonomy, add case-specific rules, change public skill
texts, alter RAG, memory, guardrails, retry policy, generation settings, frontend, or
presentation behavior. `AnswerContract` keeps its name and remains the clinical semantic layer.

REQUESTSPEC_COMMIT: the production commit containing this document, with commit message
`Add request-spec completion coverage`; the resolved hash is recorded in the final handoff.
