from __future__ import annotations

import json
from collections import deque
from collections.abc import Iterable
from typing import Any

from medagent.llm.client import LLMResponse


class ScriptedLLM:
    """Predictable async client used by public tests and parity fixtures."""

    def __init__(self, responses: Iterable[LLMResponse | str | dict[str, Any]]) -> None:
        self.responses = deque(responses)
        self.requests: list[dict[str, Any]] = []
        self.closed = False

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse:
        self.requests.append(
            {"messages": messages, "tools": tools or [], "response_format": response_format}
        )
        if not self.responses:
            return LLMResponse("No scripted response remains.")
        response = self.responses.popleft()
        if isinstance(response, LLMResponse):
            return response
        if isinstance(response, dict):
            return LLMResponse(json.dumps(response, ensure_ascii=False))
        return LLMResponse(response)

    async def close(self) -> None:
        self.closed = True


FakeLLM = ScriptedLLM


class DeterministicLLM:
    """Offline-safe local response generator for installation and CLI smoke tests."""

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse:
        prompt = str(messages[-1].get("content", "")) if messages else ""
        lower = prompt.casefold()
        if response_format or "planner" in lower:
            question = lower.split("question:", 1)[-1].split("\ncontract:", 1)[0]
            worker = "consultation_agent" if "treatment" in question else "diagnostic_agent"
            subtasks = [
                {"subtask_id": "task-1", "description": prompt[-500:], "assigned_agent": worker}
            ]
            if any(word in question for word in ("evidence", "guideline", "research")):
                subtasks.append(
                    {
                        "subtask_id": "task-2",
                        "description": "Review admitted evidence for the requested deliverable.",
                        "assigned_agent": "research_agent",
                    }
                )
            return LLMResponse(json.dumps({"subtasks": subtasks}, ensure_ascii=False))
        if "synthesize" in lower:
            try:
                payload = json.loads(prompt.split("\n", 1)[1])
                drafts = [item["answer"] for item in payload["worker_drafts"] if item["answer"]]
                unique = list(dict.fromkeys(drafts))
                return LLMResponse("Clinical synthesis\n\n" + "\n\n".join(unique))
            except (IndexError, KeyError, TypeError, json.JSONDecodeError):
                return LLMResponse("Clinical synthesis unavailable.")
        return LLMResponse(
            "Clinical assessment\n\nThe available case facts are limited. Confirm the history, "
            "examination findings, urgent warning signs, and decision-changing tests before "
            "drawing a clinical conclusion.\n\nThis output is informational and requires "
            "qualified clinical review."
        )

    async def close(self) -> None:
        return None
