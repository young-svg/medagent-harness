from __future__ import annotations

import json
import re
from typing import Any

from medagent.context.request_spec import RequestSpec


def has_answer_content(answer: str) -> bool:
    """Reject structural headings; retain punctuated bold prose without keyword checks."""
    lines = answer.splitlines()
    setext_lines: set[int] = set()
    paragraph_start = 0
    for index, line in enumerate(lines):
        if not line.strip():
            paragraph_start = index + 1
        elif index > paragraph_start and re.fullmatch(r" {0,3}(?:=+|-+)\s*", line):
            setext_lines.update(range(paragraph_start, index + 1))
            paragraph_start = index + 1
    for index, line in enumerate(lines):
        if index in setext_lines:
            continue
        text = line.strip()
        if not text or text.endswith((":", "：")):
            continue
        if re.match(r"^#{1,6}\s", text):
            # An inline labelled answer still contains a body after the colon.
            if not re.search(r"[:：]\s*\w", text):
                continue
        bold = re.fullmatch(r"\*\*([^*]+)\*\*|__([^_]+)__", text)
        if bold:
            body = (bold.group(1) or bold.group(2)).strip()
            # A bare bold label is ambiguous; sentence punctuation or an inline
            # labelled body distinguishes prose without interpreting medical terms.
            if not re.search(r"[.!?。！？;；]|[:：]\s*\w", body):
                continue
        if re.search(r"[^\W_]", text, re.UNICODE):
            return True
    return False


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def parse_synthesis_answers(content: str, spec: RequestSpec) -> tuple[dict[str, str], str]:
    """Fail closed on ambiguous IDs/structure; missing content can be backfilled."""
    try:
        value = json.loads(content, object_pairs_hook=_unique_keys)
    except (ValueError, RecursionError):
        return {}, "invalid_json"
    if not isinstance(value, dict) or set(value) != {"answers"}:
        return {}, "invalid_structure"
    if not isinstance(value["answers"], list):
        return {}, "invalid_structure"
    valid_ids = {item.id for item in spec.items}
    answers: dict[str, str] = {}
    seen: set[str] = set()
    for item in value["answers"]:
        if not isinstance(item, dict) or set(item) != {"request_item_id", "answer"}:
            return {}, "invalid_structure"
        request_id, answer = item["request_item_id"], item["answer"]
        if not isinstance(request_id, str) or request_id not in valid_ids:
            return {}, "invalid_request_id"
        if request_id in seen:
            return {}, "duplicate_request_id"
        if not isinstance(answer, str):
            return {}, "invalid_structure"
        seen.add(request_id)
        if has_answer_content(answer):
            answers[request_id] = answer.strip()
    complete = all(item in answers for item in spec.required_item_ids)
    return answers, "complete" if complete else "partial"
