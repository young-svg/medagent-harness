# RequestSpec Completion Refactor — Design and Validation

## Why the old AnswerContract missed requests

AnswerContract is a closed clinical taxonomy. A question can explicitly ask three things while
only one maps to a known deliverable. Using deliverable coverage as the sole completion gate made
`ContractComplete` possible even when two explicit user requests were absent. The invariant that
failed was `ContractComplete != UserRequestComplete`.

## Implemented design

RequestSpec is the source-backed, open-world request layer. Its RequestItems keep stable IDs,
source order, original spans, required/optional status, and an optional clinical semantic mapping.
Deterministic list parsing supports numeric, full-width, parenthesized, circled, bullet, and clear
newline question lists. An unsafe split falls back to one lossless item.

UNKNOWN mappings are never deleted. They remain required and participate in planning, worker
output, coverage, and the completion gate without adding new medical taxonomy labels.

Planner subtasks now carry `request_item_ids` beside existing `deliverable_ids`. Policy validation
ensures every subtask maps at least one RequestItem and every required RequestItem maps to at least
one subtask. Missing mappings are filled deterministically on existing workers; there is no repair
LLM call. Same-role items may still merge into one worker.

Workers return per-item `RequestItemAnswer` entries. Coverage comes from actual validated,
non-empty answers, never assigned IDs. A partial response retains its valid answers but leaves
omitted IDs missing. Provider failures cover nothing. Duplicate worker coverage is unioned.

The primary gate is `UserRequestComplete`; the existing clinical gate remains secondary.
A completed run requires both `user_request_complete=true` and `contract_complete=true`.
Single complete workers still bypass synthesis; multi-worker synthesis receives RequestSpec and
per-item answers.

## Synthetic tests

The new suite covers all requested invariants:

1. explicit three-part numbered/full-width/parenthesized/circled/bullet extraction;
2. required UNKNOWN preservation;
3. three same-role requests merged to one worker;
4. worker omission produces a missing RequestItem and incomplete run;
5. complete per-item answers satisfy request coverage;
6. duplicate coverage uses set union;
7. missing optional item does not block completion;
8. an unstructured single question is preserved losslessly;
9. existing-test analysis and prospective further-tests remain separate RequestItems and only the
   prospective request maps to `FURTHER_TESTS`;
10. four ScriptedLLM smoke paths: same-role single, cross-role multi, partial incomplete, and
    UNKNOWN complete.

Full offline result: `96 passed` (80 existing tests preserved plus 16 new tests).

## Tooling checks

- Focused Ruff check for all modified production/test/validation code: PASS.
- `git diff --check`: PASS.
- Full `ruff check .`: blocked only by the pre-existing untracked
  `eval/run_legacy_milvus_server.py` import-order finding; that unrelated file was not modified.

## Targeted development validation

Only MED-050, MED-054, MED-055, and MED-060 were run. Input was restricted to `eval_id`,
`description`, and `question`; no reference answer, DeepSeek answer, manual score, judge, or
quality scoring was read or used.

| Case | RequestItems | Planner mapping | Per-item answers | UserRequestComplete | ContractComplete | Final |
|---|---:|---|---|---|---|---|
| MED-050 | 3 | complete | 3/3 | true | true | non-empty |
| MED-054 | 3 | complete | 3/3 | true | true | non-empty |
| MED-055 | 3 | complete | 3/3 | true | true | non-empty |
| MED-060 | 3 | complete | 3/3 | true | true | non-empty |

MED-060 specifically preserves ERAS benefits, preoperative items, and postoperative items as
RQ1/RQ2/RQ3. UNKNOWN semantic mappings do not prevent any of the three required answers from
being tracked or completed.

## Modified files

- `medagent/context/request_spec.py`
- `medagent/context/contract.py`
- `medagent/planning/models.py`
- `medagent/planning/planner.py`
- `medagent/agents/base.py`
- `medagent/runtime/agent_loop.py`
- `medagent/runtime/coverage.py`
- `medagent/runtime/native_engine.py`
- `medagent/observability/schema.py`
- `medagent/observability/tracer.py`
- `tests/test_request_spec_completion.py`
- `docs/REQUESTSPEC_ARCHITECTURE.md`
- `eval/requestspec_validation/run_targeted_validation.py`
- `eval/requestspec_validation/REQUESTSPEC_DESIGN.md`
- `eval/requestspec_validation/REQUESTSPEC_TESTS.txt`
- `eval/requestspec_validation/TARGETED_VALIDATION.json`
- `eval/requestspec_validation/TARGETED_VALIDATION.md`

REQUESTSPEC_COMMIT: self, commit message `Add request-spec completion coverage`; the resolved
commit hash is recorded in the final handoff.
