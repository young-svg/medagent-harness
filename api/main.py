from __future__ import annotations

import os
from contextlib import asynccontextmanager
from hashlib import sha256
from ipaddress import ip_address
from pathlib import Path
from secrets import compare_digest
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from api.schemas import AnalyzeRequest, AnalyzeResponse
from medagent.guardrails.sanitizer import sanitize_answer
from medagent.memory.session import resolve_session_id
from medagent.observability.privacy import metadata_payload
from medagent.observability.replay import trace_summary
from medagent.runtime.config import RuntimeConfig
from medagent.runtime.coordinator import Coordinator
from medagent.runtime.engine import (
    EngineExecutionError,
    RuntimeConfigurationError,
    StageGenerationError,
)

_config = RuntimeConfig.from_env()
_coordinator = Coordinator(config=_config)
_access_token = os.getenv("MEDAGENT_API_TOKEN", "")
_run_sessions: dict[str, tuple[str, str]] = {}


def require_access(request: Request) -> str:
    """One trusted local operator, optionally protected by a configured secret."""
    try:
        local = request.client is not None and ip_address(request.client.host).is_loopback
        host = request.headers.get("host", "")
        parsed = urlsplit("http://" + host)
        valid_host = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        valid_host = (valid_host and parsed.username is None and parsed.path == ""
                      and parsed.netloc == host and not parsed.query and not parsed.fragment)
        _ = parsed.port
    except ValueError:
        local = valid_host = False
    if not local or not valid_host:
        raise HTTPException(status_code=403, detail="trusted loopback access required")
    origin = request.headers.get("origin")
    if ((origin is not None and origin != f"{request.url.scheme}://{host}")
            or request.headers.get("sec-fetch-site") == "cross-site"):
        raise HTTPException(status_code=403, detail="cross-origin access denied")
    if _access_token:
        supplied = request.headers.get("authorization", "")
        if not compare_digest(supplied.encode(), ("Bearer " + _access_token).encode()):
            raise HTTPException(status_code=401, detail="authorization required")
        return sha256(_access_token.encode()).hexdigest()
    return "trusted-local-operator"


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await _coordinator.close()


app = FastAPI(title="MedAgent Harness", version="2.0.0", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def invalid_request(_: Request, error: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": [
        {"loc": item["loc"], "type": item["type"], "msg": "Invalid request field"}
        for item in error.errors()
    ]})


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "mode": _coordinator.mode, "long_term_memory": "excluded"}


@app.post("/api/analyze", response_model=AnalyzeResponse)
async def analyze(
    request: AnalyzeRequest, principal: str = Depends(require_access),
) -> dict[str, object]:
    try:
        session_id = resolve_session_id(request.session_id)
        result = await _coordinator.analyze(request.description, request.question, session_id)
        _run_sessions[result["run_id"]] = (session_id, principal)
        result = {**result, "session_id": session_id, "trace": metadata_payload(result["trace"])}
        result["request_item_answers"] = {
            key: sanitize_answer(value)
            for key, value in result.get("request_item_answers", {}).items()
        }
        result["presentation"] = {**result["presentation"], "execution_summary": result["trace"]}
        return result
    except RuntimeConfigurationError:
        raise HTTPException(status_code=503, detail="runtime configuration unavailable") from None
    except StageGenerationError as error:
        if error.failure_stage == "planning" and error.failure_reason == "subtask_budget_exhausted":
            raise HTTPException(status_code=422, detail={
                "failure_stage": "planning", "failure_reason": "subtask_budget_exhausted",
            }) from None
        raise HTTPException(status_code=502, detail="analysis execution failed") from None
    except EngineExecutionError:
        raise HTTPException(status_code=502, detail="analysis execution failed") from None
    except Exception:
        raise HTTPException(status_code=500, detail="internal execution error") from None


@app.get("/api/runs/{run_id}")
async def get_run(
    run_id: str, session_id: str | None = Query(default=None, min_length=1, max_length=128,
                                              pattern=r"^[A-Za-z0-9_.-]+$"),
    principal: str = Depends(require_access),
) -> dict[str, object]:
    try:
        canonical = str(UUID(run_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="run not found") from None
    if canonical != run_id or _run_sessions.get(run_id) != (session_id, principal):
        raise HTTPException(status_code=404, detail="run not found")
    root = Path(_config.trace_dir).resolve()
    run_path = root / run_id
    if (run_path.resolve().parent != root
            or (run_path / "trace.jsonl").resolve().parent != run_path.resolve()):
        raise HTTPException(status_code=404, detail="run not found")
    try:
        return metadata_payload(trace_summary(run_path))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="run not found") from None
