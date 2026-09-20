from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from medagent.agents.consultation import ConsultationAgent
from medagent.agents.diagnostic import DiagnosticAgent
from medagent.agents.research import ResearchAgent
from medagent.context.contract import build_answer_contract
from medagent.context.evidence_ledger import build_evidence_ledger
from medagent.guardrails.contract_checker import ContractChecker
from medagent.guardrails.sanitizer import sanitize_answer
from medagent.guardrails.stable_patch import apply_stable_patch
from medagent.memory.session import SessionMemory
from medagent.observability.tracer import TraceRecorder
from medagent.planning.planner import Planner
from medagent.planning.router import Router
from medagent.presentation.adapter import PresentationAdapter
from medagent.retrieval.admission import admit_evidence
from medagent.retrieval.backend import NullRetrievalBackend, RetrievalBackend
from medagent.retrieval.collection_router import CollectionRouter
from medagent.retrieval.config import RetrievalConfig
from medagent.retrieval.evidence import EvidenceBundle
from medagent.retrieval.query_builder import QueryBuilder
from medagent.runtime.agent_loop import AgentLoop
from medagent.runtime.config import RuntimeConfig


class Coordinator:
    """Freeze-v2 centralized planner-worker runtime."""

    AGENTS = {
        "diagnostic_agent": DiagnosticAgent,
        "consultation_agent": ConsultationAgent,
        "research_agent": ResearchAgent,
    }

    def __init__(
        self,
        config: RuntimeConfig | None = None,
        memory: SessionMemory | None = None,
        backend: RetrievalBackend | None = None,
    ) -> None:
        self.config = config or RuntimeConfig.from_env()
        self.memory = memory or SessionMemory()
        self.backend = backend or NullRetrievalBackend()

    def analyze(self, description: str, question: str, session_id: str) -> dict[str, Any]:
        trace = TraceRecorder(self.config.trace_dir)
        try:
            trace.record(
                "run_start", {"session_id": session_id, "runtime": self.config.public_dict()}
            )
            history = self.memory.context(session_id, description)
            trace.record("context", {"description": description, "question": question})
            trace.record(
                "memory", {"type": "session", "messages": history, "long_term": "disabled"}
            )

            contract = build_answer_contract(question)
            ledger = build_evidence_ledger(description)
            trace.record("contract", contract.to_dict())
            trace.record("ledger", ledger.to_dict())

            plan = Planner().plan(question, contract)
            route = Router().route(plan)
            trace.record("plan", plan.to_dict())
            trace.record("route", route.to_dict())

            retrieval_config = RetrievalConfig(
                generic_collection=self.config.generic_collection,
                special_collection=self.config.special_collection,
                embedding_model=self.config.embedding_model,
            )
            collection_decision = CollectionRouter(retrieval_config).route("search_knowledge")
            query = QueryBuilder().build(
                question, plan.subtasks[0].description, contract.requested_deliverables[0]
            )
            trace.record("retrieval_query", {"query": query, **asdict(collection_decision)})
            items = self.backend.search(
                query, collection_decision.collection, retrieval_config.top_k
            )
            admit_evidence(
                items, retrieval_config.admission_threshold, retrieval_config.max_admitted_evidence
            )
            bundle = EvidenceBundle(query, collection_decision.collection, items)
            trace.record("retrieval_result", bundle.to_dict())

            workers = [self.AGENTS[name]() for name in route.workers]
            results = AgentLoop(self.config.swarm_timeout).execute(
                workers, question, contract, ledger, bundle
            )
            for result in results:
                trace.record("worker_draft", result.to_dict())
            successful = [result.answer for result in results if result.success and result.answer]
            final_answer = "\n\n".join(successful) or "未能生成有效分析。"
            trace.record(
                "synthesis",
                {
                    "status": "completed" if successful else "failed",
                    "worker_count": len(successful),
                },
            )

            check = ContractChecker().check(final_answer, contract)
            trace.record("checker", check.to_dict())
            patch = apply_stable_patch(final_answer, {})
            trace.record("patch", {"applied": patch.applied, "reason": patch.reason})
            final_answer = sanitize_answer(patch.answer)
            trace.record("final_answer", {"answer": final_answer})

            self.memory.add(session_id, "user", description)
            self.memory.add(session_id, "assistant", final_answer)
            trace.finish("completed")
            summary = trace.summary()
            presented = PresentationAdapter().adapt(
                final_answer,
                contract,
                ledger,
                bundle,
                {
                    **summary,
                    "plan": plan.to_dict(),
                    "route": route.to_dict(),
                    "retrieval": {
                        "query": query,
                        "collection": bundle.collection,
                        "admitted": len(bundle.admitted_items),
                    },
                    "checker": check.to_dict(),
                },
            )
            return {"answer": presented.to_dict(), "run_id": trace.run_id, "trace_summary": summary}
        except Exception as error:
            trace.record("error", {"type": type(error).__name__, "message": str(error)})
            trace.finish("failed")
            raise


def analyze_case(
    description: str,
    question: str,
    session_id: str = "default",
    trace_dir: str | Path | None = None,
) -> dict[str, Any]:
    config = RuntimeConfig.from_env()
    if trace_dir is not None:
        config = RuntimeConfig(
            llm_base_url=config.llm_base_url,
            llm_api_key=config.llm_api_key,
            llm_model=config.llm_model,
            embedding_model=config.embedding_model,
            generic_collection=config.generic_collection,
            special_collection=config.special_collection,
            milvus_uri=config.milvus_uri,
            swarm_timeout=config.swarm_timeout,
            max_tool_calls=config.max_tool_calls,
            trace_dir=str(trace_dir),
        )
    return Coordinator(config=config).analyze(description, question, session_id)
