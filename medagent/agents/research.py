from __future__ import annotations

from typing import Any

from medagent.agents.base import BaseAgent, WorkerResult
from medagent.context.contract import AnswerContract
from medagent.context.evidence_ledger import EvidenceLedger


class ResearchAgent(BaseAgent):
    agent_id = "research_agent"

    def run(
        self,
        question: str,
        contract: AnswerContract,
        ledger: EvidenceLedger,
        evidence: list[dict[str, Any]],
    ) -> WorkerResult:
        if evidence:
            ids = ", ".join(str(item["evidence_id"]) for item in evidence)
            text = f"【证据检索】\n已接纳证据：{ids}。具体内容见 Evidence Cards。"
        else:
            text = "【证据检索】\n未配置外部检索后端；未返回或伪造文献证据。"
        return WorkerResult(self.agent_id, text)
