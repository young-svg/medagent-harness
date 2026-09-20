from __future__ import annotations

from dataclasses import dataclass

from medagent.planning.models import VALID_WORKERS, Plan


@dataclass(slots=True)
class Route:
    mode: str
    workers: list[str]
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {"mode": self.mode, "workers": self.workers, "reason": self.reason}


class Router:
    def route(self, plan: Plan) -> Route:
        workers = list(
            dict.fromkeys(
                item.assigned_agent
                for item in plan.subtasks
                if item.assigned_agent in VALID_WORKERS
            )
        )
        if not workers:
            return Route("single", ["diagnostic_agent"], "no_dispatchable_worker_fallback")
        return Route(
            "single" if len(workers) == 1 else "multi",
            workers,
            "single_subtask" if len(workers) == 1 else "multiple_specialties",
        )
