# A3 frozen engineering evaluation runner

The frozen inputs and per-case pass/fail wording live only in
`docs/A3_COMPOSITE20_CASES.json`. The runner verifies its fixed SHA-256 before
execution and preserves the JSON order. It does not alter Planner plans, force
tools, or assign a semantic PASS.

Prepare a **new** external Milvus Lite path using the four synthetic texts:

```powershell
python -m eval.a3.milvus_fixture --db C:/Users/dell/AppData/Local/Temp/a3-synthetic-new.db
```

The builder requires the locally cached `BAAI/bge-small-zh-v1.5` model and
forbids downloads. It creates two COSINE/512 collections with the same four
frozen source texts in each, then checks schema, content and a real search.
Choose a new path for every build; existing databases are never overwritten.

Only **after separate approval for paid calls**, load the OpenAI-compatible API
settings into the process environment and run once with an external output path:

```powershell
python -m eval.a3.runner --execute-real --milvus-db C:/Users/dell/AppData/Local/Temp/a3-synthetic-new.db --output C:/Users/dell/AppData/Local/Temp/a3-results-new
```

The runner rejects an absent real API configuration. Its shared async lock
reserves a slot before each `OpenAICompatibleLLM.complete()` invocation; the
current client sends one HTTP POST per invocation. Both ordinary and failed
transport attempts consume slots. Worker retries, recovery, Synthesis and
Presentation use the same wrapper. Per-case limits are 5 (Single/Memory) or 7
(Multi/RAG); the total is 124. A blocked Presentation may fall back in the
runtime, but the A3 record marks the case not passed. A future client that
issues multiple HTTP requests inside one `complete()` needs a new budget audit.

`results.jsonl` is flushed after every case, including failures; there is no
automatic case rerun. `manifest.json` records the frozen hash and retrieval
preflight. Raw Trace and answers stay in the external output directory and
must not be committed. The Memory cases reuse one engine in M01–M04 order,
with distinct A/B session IDs.

For human review, copy the result ledger into a separate review record. For
each case, inspect its `final_answer`, `plan`, `route`, `worker_coverage`,
`final_coverage`, tool/retrieval evidence and Trace. Apply the unchanged
`semantic_review.rubric_pass` / `rubric_fail` text to explicit criterion results
with evidence references and a reviewer identifier. Keep `semantic_review`
as `pending` until this is done. Only a separately reviewed A/B/C result may
be reported as PASS; `completed` or covered IDs alone are insufficient.
