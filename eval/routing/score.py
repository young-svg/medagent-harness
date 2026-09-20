from __future__ import annotations


def score_route(expected: dict[str, object], observed: dict[str, object]) -> dict[str, float]:
    mode = float(expected.get("mode") == observed.get("mode"))
    expected_workers = set(expected.get("workers", []))
    observed_workers = set(observed.get("workers", []))
    exact = float(mode == 1.0 and expected_workers == observed_workers)
    return {"mode_accuracy": mode, "route_exact_accuracy": exact}
