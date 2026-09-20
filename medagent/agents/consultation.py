from __future__ import annotations

from typing import Any

from medagent.agents.base import BaseAgent, WorkerResult
from medagent.context.contract import AnswerContract
from medagent.context.evidence_ledger import EvidenceLedger


class ConsultationAgent(BaseAgent):
    agent_id = "consultation_agent"

    def run(
        self,
        question: str,
        contract: AnswerContract,
        ledger: EvidenceLedger,
        evidence: list[dict[str, Any]],
    ) -> WorkerResult:
        return WorkerResult(
            self.agent_id,
            "【咨询说明】\n当前为离线结构演示；在诊断、用药和个体风险未确认前，"
            "不生成个体化治疗建议。请由临床人员评估下一步处置。",
        )
