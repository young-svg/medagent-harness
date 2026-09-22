import type {
  AnalyzeResponse,
  BackendPresentation,
  PresentationView,
  RequestItem,
  RetrievalInfo,
  RouteInfo,
  TraceEvent,
  WorkerPlan,
} from "./types";

const plainLanguageFallback = "该结果基于病例信息和医学分析生成，详细解释见下方。";
const disclaimerFallback = "本工具仅用于医学信息与病例分析演示，不能替代专业医生的诊断和治疗。";

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function directAnswerFrom(answer: string): string {
  const firstParagraph = answer.split(/\n\s*\n/).map((part) => part.trim()).find(Boolean);
  return firstParagraph || "暂未生成可展示的结论。";
}

function requestItemsFrom(execution: Record<string, unknown>): RequestItem[] {
  const requestSpec = objectValue(execution.request_spec);
  const items = Array.isArray(requestSpec.items) ? requestSpec.items : [];
  return items.map((raw, index) => {
    const item = objectValue(raw);
    return {
      id: typeof item.id === "string" ? item.id : `RQ${index + 1}`,
      text: typeof item.text === "string" ? item.text : "Request item",
      required: item.required !== false,
      semanticType: typeof item.semantic_type === "string" ? item.semantic_type : null,
    };
  });
}

function workersFrom(execution: Record<string, unknown>): WorkerPlan[] {
  const plan = objectValue(execution.plan);
  const subtasks = Array.isArray(plan.subtasks) ? plan.subtasks : [];
  const workerResults = Array.isArray(execution.workers) ? execution.workers.map(objectValue) : [];
  return subtasks.map((raw, index) => {
    const subtask = objectValue(raw);
    const agent = typeof subtask.assigned_agent === "string" ? subtask.assigned_agent : "clinical_agent";
    const result = workerResults.find((item) => item.worker === agent || item.worker_id === agent);
    return {
      id: typeof subtask.subtask_id === "string" ? subtask.subtask_id : `ST${index + 1}`,
      name: agent,
      description: typeof subtask.description === "string" ? subtask.description : "Clinical analysis",
      requestItemIds: stringArray(subtask.request_item_ids),
      status: typeof result?.worker_status === "string" ? result.worker_status : undefined,
    };
  });
}

function routeFrom(execution: Record<string, unknown>): RouteInfo | null {
  const route = objectValue(execution.route);
  if (typeof route.mode !== "string") return null;
  return { mode: route.mode, reason: typeof route.reason === "string" ? route.reason : undefined };
}

function retrievalFrom(execution: Record<string, unknown>): RetrievalInfo | null {
  const retrieval = objectValue(execution.retrieval);
  if (!Object.keys(retrieval).length) return null;
  return {
    query: typeof retrieval.query === "string" ? retrieval.query : null,
    collection: typeof retrieval.collection === "string" ? retrieval.collection : null,
    retrievedCount: typeof retrieval.retrieved_count === "number" ? retrieval.retrieved_count : 0,
    admittedEvidenceIds: stringArray(retrieval.admitted_evidence_ids),
  };
}

function traceFrom(execution: Record<string, unknown>): TraceEvent[] {
  return Array.isArray(execution.trace_events)
    ? execution.trace_events.filter((event): event is TraceEvent => Boolean(event && typeof event === "object"))
    : [];
}

export function adaptAnalyzeResponse(response: AnalyzeResponse): PresentationView {
  const source: BackendPresentation = response.presentation || {};
  const clinicalDetail = source.clinical_detail || source.professional_answer || response.final_answer || "";
  const execution = objectValue(source.execution_summary);

  return {
    directAnswer: source.direct_answer || directAnswerFrom(clinicalDetail),
    plainLanguage: source.plain_language || source.plain_language_summary || plainLanguageFallback,
    clinicalDetail,
    disclaimer: source.disclaimer || disclaimerFallback,
    evidenceCards: source.evidence_cards || [],
    requestItems: requestItemsFrom(execution),
    route: routeFrom(execution),
    workers: workersFrom(execution),
    retrieval: retrievalFrom(execution),
    traceEvents: traceFrom(execution),
  };
}
