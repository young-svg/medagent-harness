# MedAgent Showcase Demo Cases

These four fixtures are synthetic, deterministic presentation examples. They exist only to demonstrate the frontend and developer observability surfaces. Loading one in the **Demo Cases** dropdown does not call an LLM, a retrieval service, or the backend API.

| Fixture | Capability shown | Route / worker |
| --- | --- | --- |
| `demo_01_simple_single.json` | RequestSpec coverage and a three-tier answer | `single` / Consultation Agent |
| `demo_02_multi_agent.json` | Multi-agent decomposition and request ownership | `multi` / Diagnostic + Consultation Agents |
| `demo_03_guideline_rag.json` | Tool invocation and AVAILABLE evidence-card rendering | `single` / Research Agent |
| `demo_04_memory_followup.json` | Same-session history injection and new-session isolation | `single` / Consultation Agent |

Every fixture exposes the same top-level contract:

```json
{
  "question": "...",
  "presentation": {
    "direct_answer": "...",
    "plain_language": "...",
    "clinical_detail": "..."
  },
  "developer": {
    "request_spec": {},
    "planner": {},
    "workers": [],
    "tools": [],
    "evidence": {},
    "trace": []
  }
}
```

The RAG fixture is intentionally and visibly synthetic. `DEMO FIXTURE — not a real medical source` must remain on its evidence card. None of these answers or evidence records may be represented as model output, benchmark output, a real patient record, or a real clinical source.
