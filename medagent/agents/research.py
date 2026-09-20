from __future__ import annotations

from medagent.agents.base import BaseAgent, WorkerResult
from medagent.context.contract import AnswerContract
from medagent.context.evidence_ledger import EvidenceLedger
from medagent.retrieval.evidence import EvidenceBundle


class ResearchAgent(BaseAgent):
    agent_id = "research_agent"

    def run(
        self,
        question: str,
        contract: AnswerContract,
        ledger: EvidenceLedger,
        evidence: EvidenceBundle,
    ) -> WorkerResult:
        if evidence.admitted_items:
            ids = ", ".join(item.evidence_id for item in evidence.admitted_items)
            text = f"【证据检索】\n已接纳证据：{ids}。具体内容见 Evidence Cards。"
        else:
            text = "【证据检索】\n未配置外部检索后端；未返回或伪造文献证据。"
        return WorkerResult(self.agent_id, text)
