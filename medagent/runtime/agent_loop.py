from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError

from medagent.agents.base import BaseAgent, WorkerResult
from medagent.context.contract import AnswerContract
from medagent.context.evidence_ledger import EvidenceLedger
from medagent.retrieval.evidence import EvidenceBundle


class AgentLoop:
    def __init__(self, timeout_seconds: float = 180.0) -> None:
        self.timeout_seconds = timeout_seconds

    def execute(
        self,
        agents: list[BaseAgent],
        question: str,
        contract: AnswerContract,
        ledger: EvidenceLedger,
        evidence: EvidenceBundle,
    ) -> list[WorkerResult]:
        if not agents:
            return []
        with ThreadPoolExecutor(max_workers=len(agents)) as pool:
            futures = [
                pool.submit(agent.run, question, contract, ledger, evidence) for agent in agents
            ]
            results: list[WorkerResult] = []
            for future in futures:
                try:
                    results.append(future.result(timeout=self.timeout_seconds))
                except TimeoutError:
                    results.append(WorkerResult("unknown", "", success=False))
            return results
