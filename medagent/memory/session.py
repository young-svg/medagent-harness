from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass


@dataclass(slots=True)
class Message:
    role: str
    content: str


class SessionMemory:
    """Bounded, process-local conversation memory with session isolation."""

    def __init__(self, recent_limit: int = 10) -> None:
        if recent_limit < 1:
            raise ValueError("recent_limit must be positive")
        self.recent_limit = recent_limit
        self._messages: dict[str, list[Message]] = defaultdict(list)

    def add(self, session_id: str, role: str, content: str) -> None:
        normalized = content.strip()
        if not normalized:
            return
        messages = self._messages[session_id]
        if any(item.role == role and item.content == normalized for item in messages[-2:]):
            return
        messages.append(Message(role, normalized))
        self._messages[session_id] = messages[-self.recent_limit :]

    def context(self, session_id: str, current_input: str) -> list[dict[str, str]]:
        normalized = current_input.strip()
        previous = [
            asdict(item)
            for item in self._messages.get(session_id, [])
            if not (item.role == "user" and item.content == normalized)
        ]
        return [*previous[-(self.recent_limit - 1) :], {"role": "user", "content": normalized}]

    def clear(self, session_id: str) -> None:
        self._messages.pop(session_id, None)
