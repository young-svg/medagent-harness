from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException

from api.schemas import AnalyzeRequest, AnalyzeResponse
from medagent.memory.session import SessionMemory
from medagent.observability.replay import trace_summary
from medagent.runtime.config import RuntimeConfig
from medagent.runtime.coordinator import Coordinator

app = FastAPI(title="MedAgent Harness", version="1.0.0")
_config = RuntimeConfig.from_env()
_coordinator = Coordinator(config=_config, memory=SessionMemory())


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "long_term_memory": "disabled"}


@app.post("/api/analyze", response_model=AnalyzeResponse)
def analyze(request: AnalyzeRequest) -> dict[str, object]:
    return _coordinator.analyze(request.description, request.question, request.session_id)


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict[str, object]:
    if any(char not in "0123456789abcdef-" for char in run_id.lower()):
        raise HTTPException(status_code=404, detail="run not found")
    run_dir = Path(_config.trace_dir) / run_id
    try:
        return trace_summary(run_dir)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="run not found") from error
