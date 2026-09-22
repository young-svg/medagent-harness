# Demo Cases

## Background

MedAgent Harness includes four deterministic showcase cases for product demonstrations. They exercise presentation and observability states without waiting for a provider and without changing the frozen runtime. Selecting a case from **Demo Cases** loads local fixture data into the same presentation adapter used by normal API responses; it does not call `/api/analyze`, an LLM, or retrieval.

All clinical content is synthetic and intended only to show UI behavior. It is not medical advice. The guideline evidence is explicitly labeled as a fake demo source so it cannot be confused with retrieved clinical evidence.

## Demo 01 — Simple single-agent

- Capability: one Consultation Agent covers RQ1/RQ2/RQ3 on a `single` route.
- Execution: Question → RequestSpec → Planner → Consultation Agent → Checker → Final.
- Presentation: Direct Answer, Plain Language, and Clinical Detail; external evidence is `NOT_REQUIRED`.
- Fixture: `examples/demo_cases/demo_01_simple_single.json`
- Screenshot: `artifacts/demo/demo_simple.png`

## Demo 02 — Multi-agent reasoning

- Capability: independent diagnostic and management workstreams.
- Execution: Question → RequestSpec → Planner → Diagnostic Agent + Consultation Agent → Checker → Final.
- Presentation: `multi` route, four request items, two successful workers, and explicit request ownership.
- Fixture: `examples/demo_cases/demo_02_multi_agent.json`
- Screenshot: `artifacts/demo/demo_multi.png`

## Demo 03 — Guideline / RAG

- Capability: required external evidence, Research Agent, `clinical_guideline` tool status, and an AVAILABLE evidence card.
- Execution: Question → RequestSpec → Planner → Tool call/result → Research Agent → Checker → Final.
- Evidence safety: the source, section, and preview are synthetic and visibly marked `DEMO FIXTURE`; they are not a real guideline or clinical source.
- Fixture: `examples/demo_cases/demo_03_guideline_rag.json`
- Screenshot: `artifacts/demo/demo_rag.png`

## Demo 04 — Multi-turn memory

- Capability: a second-round follow-up consumes two messages from the same demo session while a new session reports zero history.
- Execution: Question → Memory read → RequestSpec → Planner → Consultation Agent → Checker → Final.
- Presentation: session ID, injected-history count and content, and isolated-session history count are visible in Developer Mode.
- Fixture: `examples/demo_cases/demo_04_memory_followup.json`
- Screenshot: `artifacts/demo/demo_memory.png`

## Running the showcase

1. Start the frontend with `npm run dev` from `web/`.
2. Choose one of the four entries in **Demo Cases**.
3. Expand **Clinical Detail** and enable **Developer Mode**.
4. Inspect the RequestSpec, route, workers, Tool / Evidence state, execution trace, and—where applicable—memory continuity.

Choosing a demo immediately loads local fixture content. The **Analyze** button remains the separate real API path and is not used by the demo loader.
