from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass


def estimate_history_tokens(messages: list[dict[str, str]]) -> int:
    """Conservative heuristic, NOT a tokenizer count or provider context limit.

    Charge one estimated token per UTF-8 byte of the JSON list (including roles,
    escaping and separators), plus eight per message as framing allowance. This
    deliberately overestimates ordinary text; model-specific tokenization and
    framing can differ. Empty history consumes zero of the history-only budget.
    """
    if not messages:
        return 0
    return len(json.dumps(messages, ensure_ascii=False).encode("utf-8")) + 8 * len(messages)


@dataclass(slots=True)
class Message:
    role: str
    content: str


class SessionMemory:
    """Process-local session memory with count and estimated history budgets.

    ``token_budget=0`` excludes history from reads but still stores messages;
    ``None`` disables only the token budget. An explicit ``enabled=False``
    disables both reads and writes without clearing stored history. Budgets
    apply at read time, never to the current input or to stored message text.
    """

    def __init__(
        self, recent_limit: int = 10, *, enabled: bool = True, token_budget: int | None = 4096
    ) -> None:
        if recent_limit < 1:
            raise ValueError("recent_limit must be positive")
        self.recent_limit = recent_limit
        self.enabled = enabled
        if token_budget is not None and (type(token_budget) is not int or token_budget < 0):
            raise ValueError("token_budget must be a non-negative integer or None")
        self.token_budget = token_budget
        self._messages: dict[str, list[Message]] = defaultdict(list)

    def add(self, session_id: str, role: str, content: str) -> bool:
        if not self.enabled:
            return False
        normalized = content.strip()
        if not normalized:
            return False
        messages = self._messages[session_id]
        if any(item.role == role and item.content == normalized for item in messages[-2:]):
            return False
        messages.append(Message(role, normalized))
        self._messages[session_id] = messages[-self.recent_limit :]
        return True

    def context(self, session_id: str, current_input: str) -> list[dict[str, str]]:
        return self.context_with_metadata(session_id, current_input)[0]

    def context_with_metadata(
        self, session_id: str, current_input: str
    ) -> tuple[list[dict[str, str]], dict[str, int | str | bool | None]]:
        """Select one history snapshot for all consumers; never trim current input."""
        normalized = current_input.strip()
        previous = (
            [
                asdict(item)
                for item in self._messages.get(session_id, [])
                if not (item.role == "user" and item.content == normalized)
            ]
            if self.enabled
            else []
        )
        slots = self.recent_limit - 1
        history = previous[-slots:] if slots > 0 else []
        count_trimmed = len(previous) - len(history)
        budget_trimmed = 0
        estimated = estimate_history_tokens(history)
        while history and self.token_budget is not None and estimated > self.token_budget:
            history.pop(0)
            budget_trimmed += 1
            estimated = estimate_history_tokens(history)
        metadata = {
            "memory_enabled": self.enabled,
            "history_token_budget": self.token_budget,
            "estimated_history_tokens": estimated,
            "estimation_method": "utf8_json_bytes_plus_8_per_message_v1",
            "available_history_messages": len(previous),
            "retained_history_messages": len(history),
            "count_trimmed_history_messages": count_trimmed,
            "budget_trimmed_history_messages": budget_trimmed,
            "trimmed_history_messages": count_trimmed + budget_trimmed,
        }
        return [*history, {"role": "user", "content": normalized}], metadata

    def clear(self, session_id: str) -> None:
        self._messages.pop(session_id, None)
