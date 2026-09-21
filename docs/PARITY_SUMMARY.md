# Semantic Parity Summary

Three synthetic scenarios were executed outside this repository against the
read-only behavior oracle and the native public runtime:

| Scenario | Result | Notes |
|---|---|---|
| Single route | MATCH | One diagnostic subtask, one diagnostic worker, terminal answer |
| Multi + tool/retrieval | MATCH | Two subtasks, multi route, synthesis, two-call budget |
| Multi-turn session | MATCH | Correction reaches the next turn and remains session-isolated |

Intentional representation differences were classified as
`ACCEPTABLE_DIFFERENCE`: the native runtime owns its wording and public trace
schema, excludes historical long-term-personalization capabilities, exposes a
full retrieval bundle only in trace, and uses bounded local session memory.

`REGRESSION` count: 0. The detailed report and executable fixtures remain in
the separate parity directory and are not distributed with the public source.
