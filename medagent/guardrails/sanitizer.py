from __future__ import annotations

import re

_PRIVATE_PATTERNS = (
    re.compile(r"<analysis>.*?</analysis>", re.IGNORECASE | re.DOTALL),
    re.compile(r"(?im)^\s*(chain[- ]of[- ]thought|private reasoning)\s*:.*$"),
)


def sanitize_answer(answer: str) -> str:
    clean = answer
    for pattern in _PRIVATE_PATTERNS:
        clean = pattern.sub("", clean)
    return clean.strip()
