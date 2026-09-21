from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException

from api.schemas import AnalyzeRequest, AnalyzeResponse
from medagent.memory.session import SessionMemory
from medagent.observability.replay import trace_summary
from medagent.runtime.config import RuntimeConfig
from medagent.runtime.coordinator import Coordinator
from medagent.runtime.engine import EngineExecutionError, RuntimeConfigurationError

_config = RuntimeConfig.from_env()
_coordinator = Coordinator(config=_config, memory=SessionMemory())


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await _coordinator.close()


app = FastAPI(title="MedAgent Harness", version="2.0.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "mode": _coordinator.mode, "long_term_memory": "excluded"}


@app.post("/api/analyze", response_model=AnalyzeResponse)
async def analyze(request: AnalyzeRequest) -> dict[str, object]:
    try:
        return await _coordinator.analyze(request.description, request.question, request.session_id)
    except RuntimeConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except EngineExecutionError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str) -> dict[str, object]:
    if any(char not in "0123456789abcdef-" for char in run_id.lower()):
        raise HTTPException(status_code=404, detail="run not found")
    try:
        return trace_summary(Path(_config.trace_dir) / run_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="run not found") from error
