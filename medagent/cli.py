from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from medagent.observability.replay import replay, trace_summary
from medagent.runtime.coordinator import analyze_case


def _run(args: argparse.Namespace) -> int:
    case = json.loads(Path(args.case).read_text(encoding="utf-8"))
    result = asyncio.run(
        analyze_case(
            case["description"], case["question"], case.get("session_id", "cli"), args.trace_root
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("api.main:app", host=args.host, port=args.port, reload=False)
    return 0


def _show(function: Callable[[str], dict[str, Any]], path: str) -> int:
    print(json.dumps(function(path), ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="medagent", description="Native MedAgent Harness CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run a JSON case with the native engine")
    run.add_argument("case")
    run.add_argument("--trace-root", default="runs")
    run.set_defaults(handler=_run)
    serve = sub.add_parser("serve", help="serve the FastAPI application")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(handler=_serve)
    trace = sub.add_parser("trace", help="print a structured trace summary")
    trace.add_argument("run_dir")
    trace.set_defaults(handler=lambda args: _show(trace_summary, args.run_dir))
    replay_parser = sub.add_parser("replay", help="read saved final output without execution")
    replay_parser.add_argument("run_dir")
    replay_parser.set_defaults(handler=lambda args: _show(replay, args.run_dir))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
