# Synthetic showcase fixtures

These files are local, deterministic presentation fixtures. They are not real patient records,
benchmark inputs, or copies of a private corpus.

| Fixture | Capability |
| --- | --- |
| `demo_01_simple_single.json` | Single-agent routing |
| `demo_02_multi_agent.json` | Multi-agent request ownership |
| `demo_03_guideline_rag.json` | Synthetic retrieval and evidence admission |
| `demo_04_memory_followup.json` | Bounded same-session memory |

Each fixture contains a synthetic case context, user task, presentation layers, and
illustrative developer metadata. Selecting a fixture in the frontend performs no model,
retrieval, or API call.

The RAG evidence cards are explicitly synthetic. Configure and govern a lawful evidence source
before using the real retrieval backend.
