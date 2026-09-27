# Static Demo

## Purpose

The static demo is an Agent Engineering portfolio showcase published through GitHub Pages. It makes the frontend workflow inspectable without deploying the Python backend, configuring a model provider, or supplying API keys.

## Features

- Four fixed scenarios covering single-agent, multi-agent, optional retrieval, and session-memory presentation.
- Multi-agent visualization with Planner route, worker ownership, and execution trace.
- Tool state and synthetic evidence-card display.
- Session-scoped memory continuity represented by local fixture data.
- Direct-answer, plain-language, clinical-detail, and developer inspection views.

## Data Flow

```text
Bundled demo fixture
  -> presentation adapter
  -> React view state
  -> answer and developer panels
```

Case switching is an in-browser data selection. No case data leaves the page.

## Limitations

- No backend runtime.
- No live inference or provider call.
- No API keys or external retrieval.
- No real-time Agent execution.
- No clinical use; the cases and evidence cards are synthetic showcase fixtures.

For implementation evidence, see [Static Demo Audit](STATIC_DEMO_AUDIT.md).
