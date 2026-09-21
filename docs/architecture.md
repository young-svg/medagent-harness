# Architecture

`NativeMedAgentEngine` owns one async lifecycle across planning, workers,
synthesis, checking, presentation, and close. The runtime is centralized: the
planner creates dispatchable subtasks and the router selects a single or multi
execution path. Workers do not autonomously claim work.

Public boundaries are protocols for the language model and retrieval backend.
Default smoke execution is deterministic and offline. Trace events record
observable inputs, outputs, tool/retrieval parent chains, latency, and usage;
they do not contain chain-of-thought.
