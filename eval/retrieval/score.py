from __future__ import annotations


def score_retrieval(expected_ids: list[str], observed_ids: list[str]) -> dict[str, float]:
    expected, observed = set(expected_ids), set(observed_ids)
    true_positive = len(expected & observed)
    precision = true_positive / len(observed) if observed else 0.0
    recall = true_positive / len(expected) if expected else 0.0
    return {"precision": precision, "recall": recall}
