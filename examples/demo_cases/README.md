# Showcase Demo Fixtures

These JSON files are synthetic, deterministic examples for demonstrating MedAgent Harness orchestration. They do not contain real patient records, model-generated benchmark answers, or real clinical evidence.

| Fixture | Showcase name | Harness capability |
| --- | --- | --- |
| `demo_01_simple_single.json` | 临床快速分析（Single Agent） | Complexity-aware `single` routing for a complete but bounded task |
| `demo_02_multi_agent.json` | 复杂病例协作分析（Multi Agent） | RequestSpec decomposition and Diagnostic/Consultation ownership |
| `demo_03_guideline_rag.json` | 循证医学分析（RAG） | Explicit evidence gating, Research Agent, tool and Evidence Card state |
| `demo_04_memory_followup.json` | 连续诊疗分析（Memory） | Same-session history injection and new-session isolation |

Every fixture contains this presentation-compatible shape:

```json
{
  "question": "explicit user task",
  "case_context": "synthetic clinical background",
  "presentation": {
    "direct_answer": "core conclusion",
    "plain_language": "patient-facing explanation",
    "clinical_detail": "full clinical detail"
  },
  "developer": {
    "request_spec": {},
    "route": {},
    "planner": {},
    "workers": [],
    "tool_state": {},
    "evidence": {},
    "trace": []
  }
}
```

The RAG example intentionally uses the source `Demo Clinical Guideline Fixture`. It must remain clearly labeled as synthetic evidence and must never be represented as a real guideline citation.
