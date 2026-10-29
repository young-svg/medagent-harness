# Static Demo

## Purpose

The static demo is an Agent Engineering portfolio showcase published through GitHub Pages. It makes the frontend workflow inspectable without deploying the Python backend, configuring a model provider, or supplying API keys.

## Features

- Four fixed scenarios covering single-agent, multi-agent, optional retrieval, and session-memory presentation.
- Multi-agent dependency example: ST2 waits for ST1, receives public RequestItemAnswer entries through a structured Handoff, then produces its own answer. The source event and illustrative event order are inspectable.
- Tool state and synthetic evidence-card display.
- A precomputed two-turn timeline representing bounded, process-local session context without patient-record persistence.
- Direct-answer, plain-language, clinical-detail, and developer inspection views.

## Data Flow

```text
Bundled demo fixture
  -> presentation adapter
  -> React view state
  -> answer and developer panels
```

Case switching is an in-browser data selection. No case data leaves the page.

The Multi example reuses `examples/demo_cases/demo_02_multi_agent.json` for its plan, Handoff and trace. These are authored, precomputed illustrations, not an execution recording or evidence that a live Planner always chooses dependencies. Upstream results are not confirmed patient facts or session memory. The runtime blocks a dependent task when upstream results are invalid; the page illustrates the successful path only.

## Local verification

From `web/`, run `npm ci`, `npm run build`, then `npm run preview -- --host 127.0.0.1`. Open the printed URL with the `/medagent-harness/` base path. Switch all four cases, inspect Developer View and clinical detail, check RAG evidence cards and the Memory timeline, then Reset. Check the Multi Handoff source event and the page status at narrow mobile widths. The favicon is bundled locally; no backend or model credentials are required.

Local A4 verification (2026-10-29): production build passed; cached Chromium smoke passed all four selectors, clinical-detail and Developer toggles, RAG cards, Memory timeline and Reset. Viewports 1440, 375 and 320 px showed no horizontal overflow or clipped status. The local favicon returned 200; no uncancelled resource failures, browser exceptions or external requests were observed. Backend regression: 459 tests passed; Ruff and `git diff --check` passed. These are local checks, not a new GitHub Pages deployment or live-model validation.

## Limitations

- No backend runtime.
- No live inference or provider call.
- No API keys or external retrieval.
- No real-time Agent execution.
- No live memory write, structured fact extraction, or persistent patient record.
- No clinical use; the cases and evidence cards are synthetic showcase fixtures.

For implementation evidence, see [Static Demo Audit](STATIC_DEMO_AUDIT.md).
