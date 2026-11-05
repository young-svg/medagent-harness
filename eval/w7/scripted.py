"""Hand-authored smoke responses, NOT reference answers or quality evaluation.

Only the model is scripted. Planning parsing/policy, tools, scheduling, handoffs
and final gates execute real project code. No dataset or rubric is generated here.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict

import httpx

from medagent.llm import LLMResponse, ScriptedLLM, ToolCall

from .budget import model_stage


def smoke_plan(case):
    items = case["expected_request_spec"]["items"]
    if case["case_id"] == "E03":
        return {
            "subtasks": [
                {
                    "subtask_id": "sources",
                    "assigned_agent": "research_agent",
                    "description": "Perform the two independent source lookups",
                    "request_item_ids": [i["id"] for i in items],
                    "deliverable_ids": ["COMPREHENSIVE_CASE_ANALYSIS"],
                    "depends_on": [],
                }
            ]
        }
    tasks = []
    for item in items:
        role = (
            "consultation_agent"
            if item["semantic_type"] in {"PREVENTION", "TREATMENT_PLAN"}
            else "diagnostic_agent"
        )
        tasks.append(
            {
                "subtask_id": item["id"],
                "assigned_agent": role,
                "description": item["text"],
                "request_item_ids": [item["id"]],
                "deliverable_ids": [item["semantic_type"]],
                "depends_on": [
                    e["from"] for e in case["expected_dependencies"] if e["to"] == item["id"]
                ],
            }
        )
    return {"subtasks": tasks}


ANSWERS = {
    "S02": {
        "RQ1": "The supplied value 7 is within the supplied reference interval 4–10. "
        "This comparison alone does not establish a diagnosis."
    },
    "I03": {
        "RQ1": "Further tests for case A: clarify duration and associated symptoms, "
        "then consider examination and investigations conditional on findings. "
        "No examination results were supplied.",
        "RQ2": "Prevention for case B: interrupt prolonged sitting with regular movement. "
        "Other risk details are unknown; no intervention has been performed.",
    },
    "D03": {
        "RQ1": "Differential diagnosis: cough can have infectious or noninfectious causes. "
        "With no examination data, these are possibilities, not a diagnosis.",
        "RQ2": "Further tests: obtain symptom duration and examination findings; consider "
        "additional investigations according to those findings. No tests were reported.",
        "RQ3": "Treatment plan and management principles: integrate the differential with "
        "the proposed assessment before choosing condition-specific management. "
        "Information is insufficient for a definitive regimen; reassess if worsening.",
    },
    "E03": {
        "RQ1": "SYNTHETIC coding test material, not a real ICD code: "
        "[EVIDENCE:SYNTHETIC-C-COUGH]. No real coding validity is claimed.",
        "RQ2": "SYNTHETIC research paper test material, not a real publication: "
        "[EVIDENCE:SYNTHETIC-R-COUGH]. No real research finding is claimed.",
    },
}


class SmokeLLM(ScriptedLLM):
    """Routing scripted responses by trusted runtime stage supports concurrent Workers."""

    def __init__(self, case, *, validation=False, fault=None):
        super().__init__([])
        self.case, self.validation, self.fault = case, validation, fault
        self.worker_requests = defaultdict(int)
        self.worker_payloads: list[dict] = []

    async def complete(self, messages, **kwargs):
        await asyncio.sleep(0.005)  # Yield to expose real Native concurrency offline.
        stage = model_stage(messages, self.validation)
        if stage == "planning":
            response = smoke_plan(self.case)
        elif stage == "acceptance":
            payload = json.loads(messages[1]["content"])
            answers = payload["final_answers"]
            missing_memory = (
                self.case["case_id"] == "M01" and not payload["bounded_session_context"]
            )
            response = {
                "items": [
                    {
                        "request_item_id": i["id"],
                        "status": "insufficient_information" if missing_memory else "satisfied",
                        "evidence": answers.get(i["id"], ""),
                        "reason": "Scripted smoke verdict; not independent quality evidence.",
                    }
                    for i in payload["request_spec"]["items"]
                ],
                "restrictions": [
                    {
                        "index": i,
                        "status": "satisfied",
                        "evidence": "",
                        "reason": "Scripted smoke restriction verdict.",
                    }
                    for i in range(len(payload["restrictions"]))
                ],
                "guardrail": [
                    {
                        "index": i,
                        "status": "false_positive",
                        "evidence": next(iter(answers.values()), ""),
                        "reason": "Scripted review only; inspect content independently.",
                    }
                    for i in range(len(payload["guardrail_issues"]))
                ],
            }
        elif stage == "synthesis":
            payload = json.loads(messages[1]["content"].split("\n", 1)[1])
            answers = {
                a["request_item_id"]: a["answer"]
                for w in payload["worker_drafts"]
                for a in w["request_item_answers"]
            }
            response = {
                "answers": [{"request_item_id": k, "answer": v} for k, v in answers.items()]
            }
        elif messages[0]["content"].startswith("You are a serialization repair"):
            payload = json.loads(messages[1]["content"])
            response = {
                "answers": [
                    {"request_item_id": i, "answer": ANSWERS[self.case["case_id"]][i]}
                    for i in payload["allowed_request_item_ids"]
                ]
            }
        else:
            payload = json.loads(messages[1]["content"])
            self.worker_payloads.append(payload)
            task = payload["subtask"]
            ids = task["request_item_ids"]
            key = task["subtask_id"]
            self.worker_requests[key] += 1
            if self.fault == "replay_after_retry" and self.worker_requests[key] == 2:
                self.requests.append({"messages": messages, **kwargs})
                raise httpx.ConnectError("synthetic continuation connection failure")
            if self.fault == "upstream_failure" and "RQ1" in ids:
                # Actual retryable transport failure. No response/usage exists.
                self.requests.append({"messages": messages, **kwargs})
                raise httpx.ConnectError("synthetic offline connection failure")
            if self.case["case_id"] == "E03" and (
                self.worker_requests[key] == 1 or self.fault == "replay_after_retry"
            ):
                calls = [
                    ToolCall(
                        f"{key}-code",
                        "disease_code",
                        {"disease_name": "SYNTHETIC cough", "request_item_id": "RQ1"},
                    ),
                    ToolCall(
                        f"{key}-paper",
                        "search_knowledge",
                        {"query": "SYNTHETIC cough papers", "request_item_id": "RQ2"},
                    ),
                ]
                calls = [c for c in calls if c.arguments["request_item_id"] in ids]
                if self.fault == "duplicate_id":
                    calls.append(calls[0])
                if self.fault == "replay_after_retry" and self.worker_requests[key] > 1:
                    calls = calls[:1]
                response = LLMResponse(tool_calls=calls)
            else:
                answers = dict(ANSWERS.get(self.case["case_id"], {}))
                if self.fault == "swapped_citations":
                    answers = {"RQ1": answers["RQ2"], "RQ2": answers["RQ1"]}
                if self.case["case_id"] == "M01":
                    history = payload["bounded_session_context"]
                    answers = {
                        "RQ1": (
                            "Previously supplied symptoms: cough and sore throat."
                            if history
                            else "Previous history is unavailable. Please provide the two symptoms."
                        )
                    }
                response = {"answers": [{"request_item_id": i, "answer": answers[i]} for i in ids]}
                if self.fault == "protocol_repair" and self.worker_requests[key] == 2:
                    response = "\n\n".join(answers[i] for i in ids)
                if self.fault == "length_recovery" and self.worker_requests[key] == 2:
                    response = LLMResponse('{"answers":[', finish_reason="length")
        # Reuse the project's ScriptedLLM response serialization and request capture.
        self.responses.append(response)
        return await super().complete(messages, **kwargs)
