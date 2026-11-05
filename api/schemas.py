from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AnalyzeRequest(BaseModel):
    description: str = Field(min_length=1, max_length=20_000)
    question: str = Field(min_length=1, max_length=5_000)
    session_id: str | None = Field(
        default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$"
    )


class AnalyzeResponse(BaseModel):
    session_id: str
    final_answer: str
    status: str
    missing_required_deliverables: list[str]
    successful_workers: int
    failed_workers: int
    run_id: str
    trace: dict[str, Any]
    presentation: dict[str, Any]
    user_request_complete: bool = False
    contract_complete: bool = False
    missing_required_request_items: list[str] = Field(default_factory=list)
    request_item_answers: dict[str, str] = Field(default_factory=dict)
    final_request_item_answers: dict[str, str] = Field(default_factory=dict)
    structural_user_request_complete: bool = False
    structural_contract_complete: bool = False
    semantic_validation: dict[str, Any] = Field(default_factory=dict)
    evidence_validation: dict[str, Any] = Field(default_factory=dict)
    execution_failures: list[dict[str, Any]] = Field(default_factory=list)
