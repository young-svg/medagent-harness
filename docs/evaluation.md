# Evaluation

No reference benchmark cases, answers, candidate outputs, or private judge data
are distributed here. The public `eval/` package contains schemas and simple
routing/retrieval/aggregation code so users can evaluate their own licensed
data.

## Recorded development result

On the fixed **CMB-Clin COMPOSITE-20 development benchmark**, the internal run
reported:

- execution success: 20/20
- RouteExactAccuracy: 90%
- worker micro-F1: 97.22%
- DispatchablePlanRate: 100%
- timeout/generation failure: 0/0

This was a development benchmark, not an unseen holdout. It measures harness
execution and routing behavior, not clinical safety or generalization.

Internal manual adjudication found overall answer quality comparable to the
contemporaneous DeepSeek web product on the fixed COMPOSITE-20 benchmark. This
was a product-level comparison, not same-model attribution; it was not unseen
generalization and used internal/manual adjudication.

