# W7 execution-layer evidence and architecture decisions

> **Integrated candidate:** the implementation described here is included in this branch. It is imported from validated `499d2fa`, with reviewed `2884f86` documentation; see [release review](FINAL_RELEASE_REVIEW.md). This is not a release-branch promotion.

Documentation snapshot: 2026-11-05. This is a public aggregate summary of existing
records, not a new experiment or a clinical evaluation. All cases are SYNTHETIC.
No answers, raw Trace, model reasoning, credentials or original HTTP bodies are
imported here. Formal independent human quality remains **UNSCORED**.

## Inputs, implementation and comparison scope

- Dataset: [frozen_v1](../experiments/w7/public_release/README.md), 24 cases / 40 RQs,
  categories 4 simple, 5 independent, 5 dependent, 6 external-evidence, 4 Memory.
  [Scoring](../experiments/w7/frozen_v1/SCORING.md) was fixed before model outputs.
- Runner: `1aa0f568d6d6cd67ffb56017bb3fab64b1759c74`.
  Live adapter: `499d2fa377903020e0785dbd25544c88acce48a0`.
  The first Pilot used the latter adapter's then-uncommitted content on the former
  base; source hashes were saved. The targeted follow-up used committed `499d2fa`.
- Model alias: `deepseek-flash`; all 59 real responses across the two batches and
  probes reported `deepseek-v4-1-flash-261017`. This is the gateway-reported label,
  not independent proof of an upstream model's identity or immutable version.
- Execution-layer inputs freeze RequestSpec, AnswerContract, restrictions and
  per-RQ evidence requirements. W1 is bypassed; W2, structural coverage, Guardrail
  hard blocks and Evidence Gate stay active. These runs do not validate real W1
  extraction or compare complete default CLI costs.
- All groups use the same configured base model, stage ceilings, shared task-tool
  capability envelope and schemas, and fresh isolated Memory seeded identically
  within each comparison. No extra Presentation LLM call is made. Native C still
  includes deterministic presentation-adapter work in its elapsed time.
- A runs one full-task AgentLoop without a Planner. B calls the real Planner and
  Policy, then executes in one general role, sequentially with structured upstream
  results. C uses NativeEngine's role-specific DAG and conditional Synthesis.
  C-serial replays C's full post-Policy plan, with concurrency 1 and no replanning.
  A/B use fixed general instructions and do not call a specialist Synthesizer;
  these are intended architecture differences, not isolated concurrency effects.
- Every run has at most 8 Worker model attempts, 8 tool attempts and 16 total model
  attempts. Global admission reserves before requests, including failures. Native
  per-subtask limits remain active. Real-mode retries/length recovery/protocol
  repair are disabled; errors stop the batch. This differs from default native
  bounded recovery. Usage without Provider data is UNKNOWN, never estimated zero.

Implementation: Runner [Runner](../eval/w7/runner.py), [live adapter](../eval/w7/live.py),
[budget and metering](../eval/w7/budget.py), [frozen inputs](../medagent/runtime/evaluation_inputs.py).

## Pilot: three cases, A/B/C, 2026-11-04

| Case | Group | Requests | Input tokens | Output tokens | Wall time (s) |
| --- | --- | ---: | ---: | ---: | ---: |
| S02 | A | 2 | 1,861 | 679 | 10.128 |
| S02 | B | 3 | 2,677 | 1,035 | 16.819 |
| S02 | C | 3 | 2,633 | 950 | 15.057 |
| I03 | A | 2 | 2,240 | 1,456 | 18.835 |
| I03 | B | 4 | 4,229 | 3,472 | 37.207 |
| I03 | C | 5 | 6,114 | 2,696 | 26.762 |
| D03 | A | 2 | 3,031 | 3,413 | 32.798 |
| D03 | B | 5 | 7,513 | 4,673 | 51.275 |
| D03 | C | 6 | 10,962 | 5,788 | 54.283 |

All nine internal states were `completed`; all independent grades are UNSCORED.
Total: **32/144** allowed requests, 41,260 input / 24,162 output tokens.
Stages: Planner 6, Worker 15, W2 9, Synthesis 2. No case invoked tools.
I03-C and D03-C reached Worker peak 2; A/B were sequential. D03 B/C received actual
structured upstream results. A has no executable task DAG; dependency-order scoring
for A is NOT_APPLICABLE at the scheduler level and needs answer review.

Separate protocol probes used 3/3 requests: JSON P1 used 1 request (71/31 tokens);
P2 used 2 (3,447/334) and entered one native read-only retrieval Handler over a
SYNTHETIC fixture, including tool-result continuation. With probes, this batch used
35 requests, 44,778 input / 24,527 output tokens. Every response was HTTP 200;
there were no failed requests, retries, unknown usage or budget overruns.

## Follow-up: same-plan DAG and Memory, 2026-11-05

| Case | Group | Memory | Requests | Input tokens | Output tokens | Wall time (s) | Internal state |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| I03 | C | On | 5 | 6,480 | 3,331 | 30.727 | completed |
| I03 | C-serial | On | 4 | 5,572 | 2,149 | 26.930 | completed |
| D03 | C | On | 6 | 11,001 | 7,350 | 67.019 | completed |
| D03 | C-serial | On | 5 | 10,490 | 9,333 | 87.864 | completed |
| M01 | A | On | 2 | 1,773 | 279 | 8.850 | completed |
| M01 | A | Off | 2 | 1,775 | 545 | 9.804 | incomplete |

All independent grades are UNSCORED. Actual total: **24/80** requests,
37,091 input / 22,987 output tokens; Planner 2, Worker 12, W2 6, Synthesis 4.
All HTTP 200; no failed request, retry, unknown usage, tool attempt or budget overrun.
M01 Off is a missing-context response, not a Provider/protocol failure.

### Shared planning and observed concurrency

Full frozen envelopes, request SHA and execution-condition SHA match within each
C/C-serial pair. Concurrency was configured at 4/1; observed Worker peaks were 2/1.
C-serial's actual Planner calls were zero. Shared planning belongs to C in the
actual batch ledger and is separately attributable to serial's full architecture
cost; do not charge it twice to actual Provider requests or batch usage.

| Case | Worker request span C / serial (s) | Planner requests | Shared input / output tokens | Shared Planner time (s) | C minus Planner time (s) | Serial plus shared Planner time (s) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| I03 | 9.150 / 15.122 | 1 | 821 / 405 | 5.988 | 24.739 | 32.918 |
| D03 | 31.480 / 51.096 | 1 | 902 / 670 | 7.537 | 59.482 | 95.401 |

Worker request span is first Worker model request start to last response end,
including dependency gaps; it is not a complete Worker lifecycle/CPU timer.
Adding/subtracting Planner request time is an accounting view, not a separately
measured execution. Serial's attributed full cost is 5 requests and 6,393/2,554
tokens for I03; 6 requests and 11,392/10,003 tokens for D03.

D03's two roots executed concurrently in C. Its third task waited for both roots
and received two structured Handoffs in both variants. Model intervals plus actual
Handoff-input checks found no dependency-order violation; this does not certify
the correctness of upstream reasoning. Different generated answer lengths and
Provider timings prevent attributing the whole elapsed-time difference to concurrency.

### Memory and unavailable retrieval comparison

M01 used the same current request/spec/contract. On supplied one bounded historical
message and returned the two supplied symptoms. Off supplied no history and asked
for the missing prior record, rather than fabricating symptoms. W2 classified Off
as `insufficient_information`; independent human quality remains UNSCORED.

D05 A/B/C are **BLOCKED**, not failures observed from real calls: the current Runner
admits only five Smoke cases, with retrieval capability enabled only for E03.
No role permission, case wording or evidence requirement was changed to work around
that limitation. Full real-model retrieval/reuse comparison has not been run.

## What is offline evidence only?

The five-case offline Smoke recorded 56 ScriptedLLM runs, including E03 RQ1 coding
retrieval and RQ2 independent research retrieval, swapped/wrong-category citations,
upstream failure blocking, tool-ID replay rejection, Memory Off and reduced-budget
failures. Those 158 model requests were scripted; their usage was UNKNOWN, not zero.
No simulated time or scripted acceptance is a real-model quality/cost result.

Tests exercise real execution adapters and shared FinalAcceptance, but scripts
supply plans, responses and W2 judgments. See [Runner tests](../tests/test_w7_runner.py),
[frozen evidence tests](../tests/test_w7_frozen_evidence_requirements.py),
[tool replay tests](../tests/test_w4_tool_call_identity.py) and
[shared acceptance tests](../tests/test_w7_shared_acceptance.py).

Latest recorded full offline suite: **986 passed in 33.70s**, after adding 13
mocked-HTTP live-adapter tests (59 targeted tests including Runner). This historical
run was not repeated for W8. E03 full live retrieval quality, real Milvus quality,
real failure recovery and broad W1 semantic accuracy remain unvalidated by W7.

## AI-assisted observations versus formal scoring

The architectural interpretations in this report are AI-assisted review of saved
texts and execution records. They are qualitative engineering observations, not a
blinded clinical assessment or a completed independent human scorecard. No numerical
answer-quality scores are assigned here.

Any AI-assisted textual first impression must remain separate from formal scoring:
it cannot change UNSCORED to PASS, cannot use internal completed/W2 satisfied as a
reference answer, and cannot establish an accuracy ranking. No archived case-level
AI preliminary grade sheet has been supplied for publication. Formal human scoring
must use the frozen rubric, named reviewer records and independently justified per-RQ
decisions. All nine Pilot answers and all six follow-ups remain UNSCORED.

## Decision and limits

A consumed fewer requests, tokens and elapsed time in the three-case Pilot.
Without human scoring this is not a quality win. B/C make ownership and dependencies
explicit, at additional planning/context/synthesis cost. Two same-plan comparisons
support that native concurrency and fan-in Handoff actually execute. Both had shorter
Worker spans under C; I03's C had slower actual end-to-end time including planning.
Keep a single-loop option; use DAG/Handoff for explicit coordination needs, and
measure latency rather than assuming more Agents improve it.

Each condition ran once, sequentially in a fixed batch order, over synthetic cases.
Cache, network variation, answer length and architectural prompt/context differences
confound general claims. No statistical significance, Multi-Agent superiority,
clinical utility or independent quality PASS is established. Memory's single case
does not validate general recall or cross-session security.

The reported experiments precede this integration. Original source IDs are retained
for provenance; the candidate has equivalent runtime snapshots, not those development
commits as Git ancestors. Offline integration validation does not revalidate or rerun
the real experiments.

Both batches plus probes used 59 requests, 81,869 input / 47,514 output tokens.
These are Provider-reported totals, not locally estimated tokens. Cache/reasoning
breakdowns are subsets and must not be added again. Monetary cost is UNKNOWN:
no verified price schedule or bill was used. Response labels do not freeze remote
Provider state. Historical [HTTP400](HTTP400_KNOWN_ISSUE.md) remains **UNKNOWN,
unresolved**; a successful current continuation does not diagnose or fix that failure.

## Provenance and public-data boundary

The two full original design reports are retained internally and omitted from this
candidate. The public frozen-data subset preserves 18 original file hashes; it does
not reproduce original-document source verification. See [public packaging](../experiments/w7/public_release/README.md).
Historical experiment metrics and original manifest hashes below are unchanged.

The aggregate tables were checked against local Pilot and targeted-run JSON plus
their SHA manifests. Original local answers, usage, plan envelopes and metadata
Trace remain separate from this document. Raw artifacts are not needed for README
rendering and are not proposed for source submission. Authorized reviewers can
use the separately prepared blind packet; its preparation is not completed grading.

| Source manifest | SHA-256 |
| --- | --- |
| Frozen dataset `freeze/manifest.json` | `33adc420b2b7e5e8117084954439daad8dba12700f241858a38c3e237f18b1cd` |
| Pilot original run manifest | `df791391e185f30069c35fa980e7550d6750c5bd1b8c49d2f7699313de9d4b0b` |
| Targeted follow-up review manifest | `89acfc47fa6b91e49975568897b7971e34ce600a23a70f467245a47ce58b4dcf` |

These identify records; they do not independently authenticate their origin.
The Runner's static `runner_source_state=uncommitted` label is stale for committed
follow-up runs. Code commit, source-file hashes and the recorded clean tracked-tree
check provide the actual provenance; original results were not rewritten to hide it.
