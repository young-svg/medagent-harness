# Demo

## Purpose

The repository includes four local fixtures that demonstrate orchestration behavior without a model endpoint or private data. All people, histories, results, evidence cards, and session identifiers are synthetic.

| Demo | Capability |
| --- | --- |
| Single Agent | One specialist covers a bounded, internally dependent request |
| Multi Agent | Diagnostic and Consultation agents own separate request items |
| RAG | Research and Consultation agents use synthetic admitted evidence cards |
| Memory | A precomputed two-turn timeline shows bounded same-session conversation context injected into a follow-up |

Fixtures live in `examples/demo_cases/` and are loaded by the frontend as static presentation data. Selecting a demo does not call a model, external retrieval service, or API.

The Memory fixture is an illustration, not a live memory write. It does not persist a patient record, extract structured clinical facts, or survive a process restart.

## Run the showcase

```bash
cd web
npm ci
npm run dev
```

Open the local Vite URL, select a demo, and inspect:

- the direct answer;
- the clinical detail;
- RequestSpec items;
- routing and worker ownership;
- tool and evidence state;
- the observable event summary;
- the precomputed Turn 1 → bounded memory write → Turn 2 timeline and its explicit runtime boundaries.

## Fixture rules

- Do not add real patient records or copied benchmark cases.
- Do not embed private-corpus excerpts or claim synthetic evidence is authoritative.
- Use obvious synthetic identifiers.
- Keep clinical content informational and subject to qualified review.
- Keep developer trace metadata illustrative; do not present it as a live production trace.

The RAG fixture intentionally uses short synthetic guideline excerpts to demonstrate the admission and evidence-card pipeline. Users must configure and govern their own lawful evidence source for real retrieval.
