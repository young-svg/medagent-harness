from __future__ import annotations

from medagent.agents.base import BaseAgent, WorkerResult
from medagent.context.contract import AnswerContract
from medagent.context.evidence_ledger import EvidenceLedger
from medagent.retrieval.evidence import EvidenceBundle


class DiagnosticAgent(BaseAgent):
    agent_id = "diagnostic_agent"

    def run(
        self,
        question: str,
        contract: AnswerContract,
        ledger: EvidenceLedger,
        evidence: EvidenceBundle,
    ) -> WorkerResult:
        facts = self.case_facts(ledger)
        answer = (
            "【病例事实】\n"
            f"{facts}\n\n"
            "【初步分析】\n"
            "当前离线演示未调用临床模型，因而不生成新的诊断事实。"
            "应由具备资质的临床人员结合完整病史、查体和检查结果形成判断。\n\n"
            "【下一步】\n"
            f"围绕用户请求“{question.strip()}”核实关键信息，并优先处理任何急症警示征象。"
        )
        return WorkerResult(self.agent_id, answer)
