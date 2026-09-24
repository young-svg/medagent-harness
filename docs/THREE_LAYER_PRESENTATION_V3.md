# Three-Layer Presentation V3

## Architecture

The Native Agent finishes its normal planning, workers, evidence handling, guardrail and `final_answer` first. A single post-answer Presentation LLM call then receives only the original case description, user question and completed professional answer. It does not invoke Planner, Worker, RAG or tools. Its output cannot change Agent completion or `final_answer`.

The three displayed layers are distinct: (1) a dynamic question-specific title plus direct conclusions/actions, (2) plain-language reasons, and (3) the exact original `final_answer` as professional medical detail. The third layer is not hidden chain-of-thought. The API retains legacy fields and adds `direct_answer_title`, `direct_answer_items` and `plain_explanation`. The frontend displays the third layer directly from `final_answer`.

## Why the JSON output protocol was replaced

The existing provider/model (`deepseek-v4-flash` through the configured OpenAI-compatible endpoint) returned schema-invalid or syntactically invalid content for all three initial live cases despite `response_format=json_object`; the run correctly fell back each time. An isolated diagnostic response had `plain_explanation` as one string instead of an array, and another had malformed JSON punctuation. A `json_schema` response-format request returned HTTP 400 from this provider, so it was not retained. Raising the presentation-only output budget did not resolve these format failures. No Agent, provider or model change was made.

## Tagged-text protocol and parser contract

The Presentation prompt now requests exactly one visible tagged output:

```text
<DIRECT_TITLE>
Question-specific title
<DIRECT_ITEMS>
- Direct conclusion or action
<PLAIN_EXPLANATION>
- Plain-language reason
<END_PRESENTATION>
```

The prompt identifies the model as an expression editor, not a diagnostic Agent. It prohibits diagnoses, medicines, doses, investigations, guidelines, risk judgments or case facts absent from the completed professional answer. It asks for 3–6 concrete direct items where possible, 2–5 short explanations with causal links, and no disclaimer/empty heading as the direct answer. The normal production call makes no JSON response-format request and never calls `json.loads` on the model response.

The deterministic parser accepts CRLF/LF, whitespace around tags, `-`/`*`/`•` bullets and a Markdown fence around the entire output. It requires the three core sections in order, a nonempty single-line title, at least one bullet in each body, and `<END_PRESENTATION>` (or an unambiguous trailing blank-line closure). It conservatively rejects missing or empty sections, truncated output, malformed bullets, duplicate/out-of-order tags and extra unknown body text. It does not guess missing content or repair malformed output.

## Fallback and observability

Exactly one Presentation LLM call occurs per real completed run. Tagged parse failure, timeout or API error causes no second model call: the run remains `completed`, the existing deterministic Direct Answer extractor and honest fixed plain-language frontend fallback are used, and the professional final answer remains exact. Offline/scripted runtimes make no unconfigured provider call; tests inject a Presentation client explicitly.

The lightweight `presentation_transform` event records model, `protocol=tagged_text`, success/fallback, parse status, latency, usage and failure reason where applicable. On a parse failure, the visible final Presentation output can be recorded for diagnosis; hidden reasoning and the Presentation prompt are not recorded. Presentation usage is included in trace token/call totals.

## Changed files

- Backend presentation: `medagent/presentation/transform.py`, `adapter.py`, `models.py`.
- Completion integration and trace schema/summary: `medagent/runtime/native_engine.py`, `medagent/observability/schema.py`, `tracer.py`.
- Frontend presentation: `web/src/types.ts`, `presentationAdapter.ts`, `App.tsx`, `components/AnswerSummary.tsx`, `components/PlainLanguageCard.tsx`, `styles.css`.
- Local showcase: `web/src/demoData.ts`.
- Tests: `tests/test_presentation_transform.py`.

No RequestSpec, AnswerContract, Planner, Worker, RAG, Memory, Guardrail, benchmark, existing medical prompt or model/provider was changed.

## Verification

- `pytest`: **137 passed**.
- `ruff check medagent tests`: **PASS**.
- `npm run build`: **PASS**.
- `git diff --check`: **PASS**.
- Browser check of all four local Demo fixtures: dynamic title, direct items, plain explanation and professional detail rendered; **zero `/api/analyze` calls**. Demo RAG had no synthetic/demo engineering text in the three user-facing answer layers. No frontend redesign was made during tagged-protocol finalization.

## Three live smoke cases

All three requested cases were rerun with the same configured real provider/model and an isolated local copy of the MedicalQA-DX Milvus Lite database. The source database and dirty main worktree were not modified. Full results and sanitized traces are under `artifacts/three_layer_presentation_v3/` (not included in the commit).

| Case | Run ID | Agent | Presentation | Final answer |
| --- | --- | --- | --- | --- |
| GERD diagnosis and management | `d7107043-53d4-465c-ae1d-f958b26fc554` | completed | tagged-text success, no fallback | exact preserved |
| Postoperative knee VTE prevention | `56aa3793-dfed-47eb-a3f3-b2d4441d13b6` | completed | tagged-text success, no fallback | exact preserved |
| Esophageal alarm case | `757c106c-c729-4097-96e7-24a57b893108` | completed | tagged-text success, no fallback | exact preserved |

GERD: the first layer gives the most likely reflux diagnosis and concrete initial treatment/lifestyle/follow-up actions. The second explains how the symptoms support that judgment and why acid suppression, lifestyle measures and reassessment help.

Postoperative knee: the first layer lists risk stratification, medication choice by the clinical team, early mechanical prevention/activity, bleeding monitoring and follow-up. The second explains postoperative thrombosis risk, how mechanical compression and muscle activity help venous return, and why anticoagulation must be balanced against bleeding.

Esophageal alarm: the first layer directly states the suspected lower-esophageal malignancy, biopsy/staging workup and stage-dependent treatment direction without a disclaimer-only heading. The second explains why swallowing difficulty, weight loss and the endoscopic abnormality require pathology and staging before treatment choice.

**Acceptance totals:** Presentation success **3/3**, fallback **0/3**, Agent completed **3/3**, exact `final_answer` preservation **3/3**, Demo API calls **0**.

The postoperative run made real Milvus queries but admitted no evidence cards. This report does not claim successful evidence grounding for that case; retrieval/admission changes are outside this Presentation task.
