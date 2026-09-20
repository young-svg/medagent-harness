from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AnalyzeRequest(BaseModel):
    description: str = Field(min_length=1, max_length=20_000)
    question: str = Field(min_length=1, max_length=5_000)
    session_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")


class AnalyzeResponse(BaseModel):
    answer: dict[str, Any]
    run_id: str
    trace_summary: dict[str, Any]
