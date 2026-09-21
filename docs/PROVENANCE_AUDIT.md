# Provenance Text Audit

Scope: 61 public production files under `medagent/` and `api/` were compared
with 121 read-only behavior-oracle production and skill files.

Checks and results:

| Check | Result |
|---|---|
| Non-trivial completely identical lines | 0 |
| Exact matching fragments of 200 or more characters | 0 |
| Eight or more consecutive identical lines | 0 |
| Long prompt or skill-text matches | 0 |
| Origin comments or copyright carryover | 0 |
| Non-public module names or local absolute paths | 0 |

Standard imports, Python syntax, short generic expressions, and ordinary data
class boilerplate were excluded from the line-level signal. One initial match
was the required public deliverable vocabulary; its representation was
independently reorganized and the scan was rerun clean.

The scan was rerun after the final Memory, Skills, RAG, and Trace wiring. It
performed read-only comparison and did not import, execute, or modify the
private behavior oracle.

`potential textual carryover: no`

This is a technical textual-similarity audit, not a legal conclusion.
