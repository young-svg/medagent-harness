from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AnalyzeRequest(BaseModel):
    description: str = Field(min_length=1, max_length=20_000)
    question: str = Field(min_length=1, max_length=5_000)
    session_id: str = Field(
        default="default", min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$"
    )


class AnalyzeResponse(BaseModel):
    final_answer: str
    status: str
    missing_required_deliverables: list[str]
    successful_workers: int
    failed_workers: int
    run_id: str
    trace: dict[str, Any]
    presentation: dict[str, Any]
