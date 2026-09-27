# Static Demo Audit

## Demo Mode Architecture

The GitHub Pages frontend is a fixture-only React/Vite showcase. `App.tsx` imports the four cases from `demoData.ts`, selects the Multi-Agent case on first render, and passes the selected fixture through `presentationAdapter.ts` into the existing answer and developer-inspection components.

Changing the case selector performs an in-memory lookup and rerenders the chosen fixture. It does not submit the case, start a runtime, or invoke a model. The developer view exposes the fixture's RequestSpec, Planner route, specialized workers, tool/evidence state, execution trace, and session-memory representation.

## Data Source

- `web/src/demoData.ts` defines the four showcase scenarios and their execution metadata.
- `web/src/demoFinalAnswers.json` contains fixed answer content.
- `web/src/demoEvidenceCards.json` contains synthetic retrieval evidence cards.
- `web/src/presentationAdapter.ts` converts fixture-shaped responses into view models.
- No patient records, provider responses, API keys, or remote datasets are loaded.

## Network Dependency

The built application requires only the static HTML, JavaScript, CSS, and bundled fixture assets served by GitHub Pages. The source contains no `fetch`, Axios, `XMLHttpRequest`, WebSocket, EventSource, external inference, or backend endpoint call. External web fonts were removed so the showcase does not depend on a third-party font request.

## API Calls

**API calls = 0**

The Vite development proxy was removed. Selecting, resetting, and inspecting demo cases remain entirely inside the browser.

## Scope

This audit covers the static showcase frontend only. It demonstrates Agent workflow and UI presentation; it does not run the MedAgent backend and is not an online medical service.
