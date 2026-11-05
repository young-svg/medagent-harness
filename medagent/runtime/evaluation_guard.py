"""Run-local checks for trusted evaluation, not a sandbox for arbitrary clients."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from medagent.llm.client import LLMClient, LLMResponse
from medagent.runtime.engine import StageGenerationError
from medagent.runtime.evaluation_inputs import canonical_json


class EvaluationConditionDriftError(StageGenerationError):
    def __init__(self, reason: str) -> None:
        super().__init__("evaluation", reason)


class EvaluationRunGuard:
    """Compare fresh live values with immutable strings; a violation stays latched."""

    def __init__(self) -> None:
        self._checks: list[tuple[str, Callable[[], Any], str]] = []
        self.error: EvaluationConditionDriftError | None = None

    def watch(self, reason: str, read: Callable[[], Any]) -> None:
        self._checks.append((reason, read, canonical_json(read())))

    def check(self) -> None:
        if self.error is not None:
            raise self.error
        for reason, read, expected in self._checks:
            try:
                same = canonical_json(read()) == expected
            except Exception:
                same = False
            if not same:
                self.error = EvaluationConditionDriftError(reason)
                raise self.error

    def wrap(self, client: LLMClient | None, *, temperature: float, max_tokens: int):
        return (_GuardedLLM(client, self, temperature, max_tokens)
                if client is not None else None)


class _GuardedLLM:
    def __init__(
        self, client: LLMClient, guard: EvaluationRunGuard, temperature: float, max_tokens: int,
    ) -> None:
        self._client = client
        self._guard = guard
        self.model = getattr(client, "model", None)
        self.temperature = getattr(client, "temperature", temperature)
        self.max_tokens = getattr(client, "max_tokens", max_tokens)

    async def complete(
        self, messages: list[dict[str, Any]], *, tools=None, response_format=None,
        max_tokens: int | None = None, temperature: float | None = None,
    ) -> LLMResponse:
        self._guard.check()
        # Standard clients honor explicit call overrides. Recovery/W2 overrides win.
        failure: Exception | None = None
        try:
            response = await self._client.complete(
                messages, tools=tools, response_format=response_format,
                max_tokens=self.max_tokens if max_tokens is None else max_tokens,
                temperature=self.temperature if temperature is None else temperature,
            )
        except Exception as error:
            failure = error
        # Check outside the transport exception handler: drift must not inherit a
        # retryable network exception context and be mistaken for infrastructure failure.
        self._guard.check()
        if failure is not None:
            raise failure
        return response

    async def close(self) -> None:
        # Original clients remain owned and closed by NativeMedAgentEngine.
        return None
