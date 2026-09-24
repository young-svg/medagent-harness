# Showcase Demo Fixtures

These JSON files are local, deterministic showcase fixtures, not real patient records or benchmark answers. Each `clinical_detail` is copied exactly from a completed real Agent `final_answer`; `source_run_id` identifies that answer's source. Direct and Why layers are presentation text; Developer workflow metadata is illustrative fixture data, not a live trace.

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

The RAG example uses hypertension instead of the previous postoperative scenario. Its evidence previews and source/section/score fields were captured from a real local MedicalQA-DX Milvus retrieval. The full corpus is not included. Corpus provenance and redistribution license still require confirmation before any public release.
