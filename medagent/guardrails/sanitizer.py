from __future__ import annotations

import re

_REASONING_TAG = re.compile(r"<\s*(/?)\s*(think|analysis)\s*>", re.IGNORECASE)
_PRIVATE_PATTERNS = (re.compile(r"(?im)^\s*(chain[- ]of[- ]thought|private reasoning)\s*:.*$"),)


def sanitize_answer(answer: str) -> str:
    """Remove reasoning blocks, including their contents and unterminated tails.

    A stack preserves nesting. An unclosed or mismatched block fails closed to
    the end of the answer; text outside balanced blocks is retained. This is
    format sanitization, not a semantic detector of unmarked reasoning.
    """
    public: list[str] = []
    stack: list[str] = []
    cursor = 0
    for match in _REASONING_TAG.finditer(answer):
        if not stack:
            public.append(answer[cursor : match.start()])
        tag = match.group(2).casefold()
        if not match.group(1):
            stack.append(tag)
        elif stack and stack[-1] == tag:
            stack.pop()
        cursor = match.end()
    if not stack:
        public.append(answer[cursor:])
    clean = "".join(public)
    for pattern in _PRIVATE_PATTERNS:
        clean = pattern.sub("", clean)
    return clean.strip()
