# Freeze v2 production baseline

`experiment_freeze_v2` is the only production-behavior baseline for this
repository. Scope Controller v3 was not promoted.

## Recorded identity and configuration

| Item | Frozen value |
|---|---|
| Checker SHA-256 | `c738b8df86d2c67bdb1225b35b01238ecec547c9fe6e897bd777996087213a9b` |
| Lead SHA-256 | `923775bfc173d6d6db1b12568dd787463aff0162d06dc8e4e0b8ab6b906fb719` |
| Requested model | `deepseek-v4-flash` |
| Observed resolved model | `deepseek-flash` |
| Temperature / max tokens | `0.2` / `4096` |
| Answer policy | `minimal_intent` |
| Swarm timeout | `180` seconds |
| Max tool calls | `2` per worker |
| Generic RAG | `medical_knowledge_v1`, fallback `medical_knowledge` |
| Guideline/code/lifestyle RAG | `medical_knowledge` |
| Embedding | `BAAI/bge-small-zh-v1.5`, observed dimension `512` |
| Admission | score ≥ `0.63`, top-k `5`, at most `5`, `1200` chars/item |
| Session memory | enabled; summary enabled; recent limit `10` |
| Long-term memory | disabled; no provider/client; no exposed tool |

The complete production-source SHA map remains in the private freeze artifact.
Representative frozen identities are:

| Source | SHA-256 |
|---|---|
| `swarm/swarm_coordinator.py` | `756d91c7b4a288d4f182d43e88513d18b83d32fbea8310be21794ef821093c0a` |
| `core/agent_loop.py` | `ad9024900cfed34e21b6ce48aeb8103199a497749d952321aa0bd7dc8659c457` |
| `core/answer_contract.py` | `7b89afb3fe4e0b4587f0a987dac0ac557c1ae6eae9000a7521ce5a473a7995ae` |
| `core/evidence_ledger.py` | `2242518ec93eaabd53e835b5cfc592e01f97b39fab847a71e1aa020b98ad23a7` |
| `runtime/trace_v2.py` | `417cf359309687f5884ef98ca4e99bd170f13793e2cea4cd5d6185ad1811ba95` |
| `retrieval/query_builder.py` | `5ee81bad55c1d0015e0f92cb34c49b995706c8178e79e7e17565b6d0422d0d6d` |
| `retrieval/collection_router.py` | `234340a2412741ea29eeea1517e5bea0efaa187f9551628132c7269d4441e6bc` |

## Behavioral mapping

The public implementation preserves centralized planning, parse fallback,
single/multi routing, the three worker roles, role-specific tool visibility,
execution-time tool denial, two-call budgets, deterministic query building,
collection routing, unified admission, session context, conservative checking,
Stable-ID patching, sanitization, Trace v2 event categories, and the 180-second
timeout. The public offline backend intentionally returns no evidence when no
licensed corpus is configured.

`search_similar_cases` was registered historically for compatibility but was
`EXPERIMENTAL_NOT_EXPOSED`. This implementation does not register, expose, or
execute it.

