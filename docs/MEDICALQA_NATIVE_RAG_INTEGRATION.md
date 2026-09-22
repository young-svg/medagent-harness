# MedicalQA Native Integration

Validation date: 2026-09-22

This integration connects an isolated copy of the existing MedicalQA-DX Milvus Lite database to the Native Retrieval Backend. It does not modify Runtime, Planner, Worker, RequestSpec, AnswerContract, prompts, orchestration, benchmark, demo fixtures, or frontend code.

## Source

- Corpus label: `MedicalQA-DX`
- Original database: `E:\agent\medix-agent-swarm\knowledge\data\milvus_lite.db`
- Isolated local copy: `E:\agent\medagent-harness\data\external_medical_kb\milvus_lite.db`
- Source payload bytes, excluding the mutable zero-byte `LOCK`: `254,572,477`
- Destination payload bytes: `254,572,477`
- Deterministic payload-tree SHA-256: `117bf43bd4b319eb761c8bde43f88ffe7f73573c0a8d56d43e00200e6c721200`
- Source/destination per-file verification: `MATCH`
- Copy protection: all 14 payload files have the Windows read-only attribute; the zero-byte
  Milvus `LOCK` remains writable so read-only clients can coordinate access.

The database directory is excluded by the repository's existing `*.db` ignore rule. It is local-only and is not part of the commit.

## Dataset

| Collection | Role | Documents | Chunks/entities |
|---|---|---:|---:|
| `medical_knowledge_v1` | General MedicalQA-DX retrieval | 52,294 | 52,830 |
| `medical_knowledge` | Small specialized legacy corpus | 10 | 30 |

Native logical-to-physical mapping:

| Native logical collection | Physical collection |
|---|---|
| `clinical_knowledge` | `medical_knowledge_v1` |
| `clinical_guidelines` | `medical_knowledge` |

The mapping is applied inside the Retrieval Backend, so no Runtime or Agent orchestration changes are required.

## Embedding

- Model: `BAAI/bge-small-zh-v1.5`
- Dimension: `512`
- Query normalization: enabled by the existing Native SentenceTransformer embedder
- Milvus metric: `COSINE`
- Admission threshold used by smoke: `0.63`
- top-k used by smoke: `5`

## Milvus

- Storage: Milvus Lite directory database
- Index: `AUTOINDEX`
- Vector field: `vector`
- Metric: `COSINE`
- General indexed rows: `52,830 / 52,830`
- Specialized indexed rows: `30 / 30`
- Dynamic fields: enabled
- Access performed by this integration: collection inspection, load, query, and search only
- Mutation APIs: not used
- Loader validation was repeated successfully after marking all payload files read-only.

The loader never creates a missing collection and never silently falls back to another collection. Missing collections, a vector dimension mismatch, a metric mismatch, unreadable `content`, or invalid `metadata` produce an explicit `RetrievalSchemaError`.

## Configuration

The external database is disabled by default. It is enabled only when both Native Milvus mode and the external source flag are configured:

```powershell
$env:MEDAGENT_RETRIEVAL_MODE = "milvus"
$env:MEDICAL_KB_ENABLED = "true"
$env:MEDICAL_KB_PATH = "E:\agent\medagent-harness\data\external_medical_kb\milvus_lite.db"
$env:MEDICAL_KB_GENERAL_COLLECTION = "medical_knowledge_v1"
$env:MEDICAL_KB_SPECIAL_COLLECTION = "medical_knowledge"
$env:MEDICAL_KB_EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"
$env:MEDICAL_KB_EXPECTED_DIMENSION = "512"
$env:MEDICAL_KB_EXPECTED_METRIC = "COSINE"
```

`MEDICAL_KB_ENABLED=true` with a non-Milvus Native mode fails explicitly. When the flag is false or unset, existing Retrieval behavior is unchanged.

## Schema compatibility

Both collections passed strict Native loader validation:

| Check | General | Specialized |
|---|---|---|
| Collection exists | PASS | PASS |
| Schema readable | PASS | PASS |
| Vector field resolves to `vector` | PASS | PASS |
| Dimension = 512 | PASS | PASS |
| Metric = COSINE | PASS | PASS |
| Dynamic fields enabled | PASS | PASS |
| `content` readable | PASS | PASS |
| `metadata` JSON parseable | PASS | PASS |

The old schema stores `content` and JSON-string `metadata` as dynamic fields. The Native adapter reads both forms and maps legacy metadata without inventing provenance.

## Retrieval smoke

Real smoke command:

```powershell
$env:HF_HUB_OFFLINE = "1"
D:\anaconda3\envs\medix-swarm\python.exe -B scripts\run_native_medicalqa_smoke.py `
  --database E:\agent\medagent-harness\data\external_medical_kb\milvus_lite.db `
  --output-dir eval\rag_native_medicalqa_smoke
```

The run used the real BGE query embedder, real Milvus Lite search, and Native score admission. No fake backend or LLM was used.

| Case | Query | Physical collection | Candidates | Accepted | Scores | Expected metadata | Result |
|---|---|---|---:|---:|---|---|---|
| 1 | 糖尿病治疗原则 | `medical_knowledge_v1` | 5 | 5 | 0.730247, 0.717628, 0.714819, 0.708539, 0.703716 | `treatment` found | PASS |
| 2 | 胃癌鉴别诊断 | `medical_knowledge_v1` | 5 | 5 | 0.746752, 0.737618, 0.729887, 0.716141, 0.711417 | `diagnosis`, `differential` found | PASS |
| 3 | 高血压生活方式管理 | `medical_knowledge_v1` | 5 | 5 | 0.703169, 0.692165, 0.687053, 0.683880, 0.680968 | `lifestyle` found | PASS |
| 4 | 请结合指南说明高血压管理 | `medical_knowledge` | 5 | 5 | 0.779495, 0.737208, 0.722455, 0.707327, 0.703057 | `clinical_guideline` route and type found | PASS |

Overall smoke result: **4/4 PASS**.

Full records, including query, collection, top-k, candidate/accepted counts, scores, metadata, evidence previews, and converted EvidenceItem values, are local artifacts:

- `eval/rag_native_medicalqa_smoke/smoke_results.json`
- `eval/rag_native_medicalqa_smoke/SMOKE_REPORT.md`

These generated smoke artifacts are not included in the integration commit.

## Evidence conversion

Each Milvus hit is converted to the Native `EvidenceItem` contract:

| EvidenceItem field | Legacy source |
|---|---|
| `evidence_id` | Stable `milvus-{hit_id}` |
| `document_id` | `metadata.doc_id`, otherwise Milvus hit ID |
| `text` | dynamic `content` |
| `source_id` | `metadata.source_record_id`, otherwise null |
| `title` | explicit title, otherwise `metadata.disease/topic`, otherwise null |
| `section` | explicit section, otherwise `metadata.type/topic`, otherwise null |
| `source` | explicit or metadata source, otherwise null |
| `url` | source URL when present, otherwise null |
| `metadata` | parsed legacy metadata object |
| `score` | Milvus COSINE distance/similarity |

Missing URL, license, source, or other provenance fields remain `null`; the adapter does not fabricate them.

## Validation

- Retrieval unit/regression tests: `19 passed`
- Ruff for modified Retrieval/script/test scope: PASS
- `git diff --check`: PASS
- Source/destination database payload checksum after real smoke: MATCH

## Limitations

- The corpus and Milvus database are local-only and are not included in the public repository.
- Corpus licensing and redistribution rights require confirmation before public or production distribution.
- MedicalQA-DX is a broad QA corpus, not a substitute for current authoritative clinical guidelines.
- The 30-chunk specialized collection has generic source labels and no reliable URL/license fields.
- Score-only admission accepted all top-5 results in this smoke, including some additional `other`, `prevention`, or `treatment` types. Expected types were present, but broader retrieval precision requires separate evaluation and threshold calibration.
- This task validates retrieval plumbing and evidence conversion only; it does not assess medical correctness, freshness, or answer quality.
- The integration does not alter Native knowledge gating. Retrieval still occurs only when the existing Runtime/Agent path calls a retrieval tool.
