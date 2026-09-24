# Runtime Deliverable Coverage Fix

## Root cause

The historical run `fa9afaad-1ebb-4ce4-a232-b53660d20c01` requested diagnosis, differential diagnosis, further tests, and treatment/follow-up. Its AnswerContract required `TREATMENT_PLAN`, `FURTHER_TESTS`, `DIFFERENTIAL_DIAGNOSIS`, and `DIAGNOSIS_WITH_BASIS`. The raw Planner assigned `FURTHER_TESTS` to `research_agent/ST2`. Because the question did not require external evidence, `apply_contract_policy` deterministically removed `ST2` (`removed_unneeded_research:ST2`). It retained `diagnostic_agent/ST1` and `consultation_agent/ST3`, but did not transfer `FURTHER_TESTS`. Therefore the explicit-mapping Contract Gate correctly reported a missing deliverable even though both Worker answers discussed further tests.

The same gap existed in Planner fallback: a surviving `consultation_agent` could receive only its role-matched `TREATMENT_PLAN`, leaving the other required deliverables ownerless before execution.

## Reconciliation location and rule

`medagent/planning/planner.py::apply_contract_policy` now runs required-deliverable ownership reconciliation **after** pruning, focused/low-scope adjustments, empty-plan fallback, same-role merging, and RequestSpec mapping, but **before** constructing the final plan that `NativeMedAgentEngine` records and routes to Workers. Both normal and Planner-fallback plans use this same step. It makes no additional Planner or LLM call and adds no Worker.

The step computes the required deliverables using the same MUST interpretation as the unchanged Contract Gate. For each deliverable missing from the surviving subtasks' `deliverable_ids` union, it assigns one existing owner deterministically:

1. If a surviving subtask owns a RequestSpec item whose `semantic_type` or explicit item text matches the deliverable, prefer among those subtasks.
2. Otherwise, prefer a surviving role suited to the deliverable: diagnostic for diagnosis/differential/tests; consultation for treatment/management; research for evidence-related work only if a Research subtask survives.
3. If that role is absent, use an existing clinical subtask; if only one subtask survives, assign every missing required deliverable to it.
4. Preserve already-complete ownership. No duplicate deliverable is added within a subtask, and no subtask is added.

The final invariant is `required_deliverables ⊆ union(surviving_subtask.deliverable_ids)`. If no surviving owner can exist, `PlanCoverageError` is raised before Worker execution rather than silently dispatching an incomplete plan. Policy actions record each deterministic reassignment.

This changes **ownership metadata and the scope shown to the chosen Worker**, not `answered_request_item_ids`, Worker structured output, or completion status. An assigned deliverable is not automatically counted complete: the Contract Gate still requires a successful Worker with a nonempty answer on the owning subtask.

## Historical Run 1: before / after

| Stage | Before | After |
| --- | --- | --- |
| Raw Planner | `ST1` diagnostic: diagnosis + differential; `ST2` research: `FURTHER_TESTS`; `ST3` consultation: treatment | Unchanged |
| Deterministic pruning | `ST2` removed because external evidence was not required | Unchanged |
| Final owner union | Diagnosis, differential, treatment; **no `FURTHER_TESTS` owner** | Diagnosis, differential, treatment, **`FURTHER_TESTS` assigned to `diagnostic_agent/ST1`** |
| Contract Gate | Historical run was incomplete despite both Workers discussing tests | Gate unchanged; it will evaluate the actual outcomes of the newly assigned Workers in any future run. The historical run was **not rerun**. |

## Fallback: before / after

For the four-deliverable contract, a Planner failure could leave one `consultation_agent/task-1` with only `TREATMENT_PLAN`. The same reconciliation now assigns the remaining required diagnosis, differential, and tests deliverables to that sole surviving Worker. This is an ownership guarantee, **not** a guarantee that the Worker will succeed or answer every section. A failed sole Worker still causes the unchanged runtime failure path.

## Regression tests and validation

`tests/test_plan_deliverable_reconciliation.py` reconstructs Run 1's raw subtask structure from the saved trace without calling a provider; checks Research pruning and diagnostic reassignment; checks RequestSpec-item ownership precedence and a single surviving consultation Worker; checks structured-parse and provider-error fallback shapes; verifies a failed Worker still leaves `FURTHER_TESTS` missing at the Contract Gate; verifies an already-complete plan is idempotent; verifies the no-owner planning error; and checks the Planner's required-item selection against the unchanged Contract Gate, including an optional deliverable.

Validation is deterministic and local: full `pytest`, `ruff check medagent tests`, and `git diff --check`. No live case, stability run, COMPOSITE-20, Presentation smoke, browser, benchmark, Judge, or push was run. Test results and the commit SHA are reported with the task handoff.

**Unchanged:** RequestSpec extraction, AnswerContract semantics, Contract Gate implementation, Worker prompt/parser/retry, infrastructure retry, Planner prompt/model parameters, Router, RAG, tools, memory, Presentation, frontend, Demo fixtures, and benchmark.
