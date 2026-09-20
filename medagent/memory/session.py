from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Protocol


@dataclass(slots=True)
class Message:
    role: str
    content: str


class LongTermMemoryProvider(Protocol):
    """Extension point only; no provider is enabled in the stable release."""

    def add(self, user_id: str, text: str) -> None: ...


class SessionMemory:
    def __init__(self, recent_limit: int = 10) -> None:
        self.recent_limit = recent_limit
        self._messages: dict[str, list[Message]] = defaultdict(list)

    def add(self, session_id: str, role: str, content: str) -> None:
        normalized = content.strip()
        if not normalized:
            return
        messages = self._messages[session_id]
        if messages and messages[-1].role == role and messages[-1].content == normalized:
            return
        messages.append(Message(role, normalized))
        self._messages[session_id] = messages[-self.recent_limit :]

    def context(self, session_id: str, current_input: str) -> list[dict[str, str]]:
        previous = [
            {"role": item.role, "content": item.content}
            for item in self._messages.get(session_id, [])
            if not (item.role == "user" and item.content == current_input.strip())
        ]
        return [*previous, {"role": "user", "content": current_input.strip()}]
