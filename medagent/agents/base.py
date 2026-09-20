from __future__ import annotations

from dataclasses import dataclass

from medagent.context.contract import AnswerContract
from medagent.context.evidence_ledger import EvidenceLedger
from medagent.retrieval.evidence import EvidenceBundle


@dataclass(slots=True)
class WorkerResult:
    worker: str
    answer: str
    success: bool = True
    tool_calls: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "worker": self.worker,
            "answer": self.answer,
            "success": self.success,
            "tool_calls": self.tool_calls,
        }


class BaseAgent:
    agent_id = "base_agent"

    def run(
        self,
        question: str,
        contract: AnswerContract,
        ledger: EvidenceLedger,
        evidence: EvidenceBundle,
    ) -> WorkerResult:
        raise NotImplementedError

    @staticmethod
    def case_facts(ledger: EvidenceLedger) -> str:
        return "；".join(item.source_text for item in ledger.findings) or "未提供病例事实"
