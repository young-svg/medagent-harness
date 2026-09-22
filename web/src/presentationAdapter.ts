import type {
  AnalyzeResponse,
  BackendPresentation,
  EvidenceCard,
  EvidenceState,
  PresentationView,
  RequestItem,
  RetrievalInfo,
  RouteInfo,
  TraceEvent,
  WorkerPlan,
} from "./types";

const plainLanguageFallback = "该结果基于病例信息和医学分析生成，详细解释见下方。";
const disclaimerFallback = "本工具仅用于医学信息与病例分析演示，不能替代专业医生的诊断和治疗。";
const retrievalTools = new Set([
  "clinical_guideline",
  "disease_code",
  "recommend_lifestyle",
  "deep_research",
  "search_knowledge",
]);

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

function stripJsonFence(value: string): string {
  const trimmed = value.trim();
  const fenced = trimmed.match(/^```(?:json)?\s*([\s\S]*?)\s*```$/i);
  return fenced ? fenced[1].trim() : trimmed;
}

function textFromJson(value: unknown, depth = 0): string | null {
  if (depth > 4) return null;
  if (typeof value === "string") {
    const candidate = stripJsonFence(value);
    try {
      return textFromJson(JSON.parse(candidate), depth + 1) || candidate;
    } catch {
      return candidate || null;
    }
  }
  if (Array.isArray(value)) {
    const parts = value.map((item) => textFromJson(item, depth + 1)).filter(Boolean);
    return parts.length ? parts.join("\n\n") : null;
  }
  if (!value || typeof value !== "object") return null;

  const item = value as Record<string, unknown>;
  for (const key of ["answer", "content", "clinical_detail"]) {
    const extracted = textFromJson(item[key], depth + 1);
    if (extracted) return extracted;
  }
  const answers = textFromJson(item.answers, depth + 1);
  return answers || null;
}

function displayText(value: string): string {
  const candidate = stripJsonFence(value);
  if (!candidate) return "";
  try {
    return textFromJson(JSON.parse(candidate)) || candidate;
  } catch {
    return value.trim();
  }
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

function evidenceStateFrom(
  execution: Record<string, unknown>,
  cards: EvidenceCard[],
  traceEvents: TraceEvent[],
): EvidenceState {
  if (cards.length > 0) return { status: "AVAILABLE" };

  const complexity = objectValue(execution.complexity_profile);
  const retrieval = objectValue(execution.retrieval);
  const retrievalAttempted = traceEvents.some((event) => {
    if (event.event_type !== "tool_call") return false;
    const payload = objectValue(event.payload);
    return typeof payload.name === "string" && retrievalTools.has(payload.name);
  });
  const retrievalFailed = traceEvents.some((event) => {
    if (event.event_type !== "tool_result" && event.event_type !== "tool_error") return false;
    const payload = objectValue(event.payload);
    if (typeof payload.name !== "string" || !retrievalTools.has(payload.name)) return false;
    const result = objectValue(payload.result);
    return Boolean(payload.error || result.error);
  });
  const evidenceRequired = complexity.requires_external_evidence === true
    || retrievalAttempted
    || typeof retrieval.query === "string" && retrieval.query.length > 0;

  if (evidenceRequired) {
    return {
      status: "REQUIRED_UNAVAILABLE",
      reason: retrievalFailed ? "retrieval backend unavailable" : "required evidence unavailable",
    };
  }
  return { status: "NOT_REQUIRED" };
}

export function adaptAnalyzeResponse(response: AnalyzeResponse): PresentationView {
  const source: BackendPresentation = response.presentation || {};
  const rawClinicalDetail = source.clinical_detail || source.professional_answer || response.final_answer || "";
  const clinicalDetail = displayText(rawClinicalDetail);
  const execution = objectValue(source.execution_summary);
  const evidenceCards = source.evidence_cards || [];
  const traceEvents = traceFrom(execution);
  const directSource = source.direct_answer ? displayText(source.direct_answer) : directAnswerFrom(clinicalDetail);
  const plainSource = source.plain_language || source.plain_language_summary;

  return {
    directAnswer: directSource,
    plainLanguage: plainSource ? displayText(plainSource) : plainLanguageFallback,
    clinicalDetail,
    disclaimer: source.disclaimer || disclaimerFallback,
    evidenceCards,
    evidenceState: evidenceStateFrom(execution, evidenceCards, traceEvents),
    requestItems: requestItemsFrom(execution),
    route: routeFrom(execution),
    workers: workersFrom(execution),
    retrieval: retrievalFrom(execution),
    traceEvents,
  };
}
