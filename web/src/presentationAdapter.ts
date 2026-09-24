import type {
  AnalyzeResponse,
  BackendPresentation,
  EvidenceCard,
  EvidenceState,
  MemoryInfo,
  PresentationView,
  RequestItem,
  RetrievalInfo,
  RetrievalSummary,
  RouteInfo,
  TraceEvent,
  ToolInfo,
  WorkerPlan,
} from "./types";

const plainLanguageFallback = "该结果基于病例信息和医学分析生成。\n展开医学详细分析查看诊断依据、鉴别诊断和治疗原则。";
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

function paragraphsFrom(answer: string): string[] {
  return answer.split(/\n\s*\n/).map((part) => part.trim()).filter(Boolean);
}

function stripDisplayMarkdown(value: string): string {
  return value
    .replace(/^\s{0,3}#{1,6}\s*/gm, "")
    .replace(/\*\*|__/g, "")
    .replace(/^\s*>\s?/gm, "")
    .replace(/^\s*[-+*]\s+/gm, "")
    .replace(/`([^`]+)`/g, "$1")
    .trim();
}

function presentationOnlyHeading(value: string): boolean {
  if (/^\s{0,3}#{1,6}\s+/u.test(value)) return true;
  const cleaned = stripDisplayMarkdown(value)
    .replace(/^\s*(?:\d+|[一二三四五六七八九十]+)[.)、．：:]?\s*/u, "")
    .trim();
  return /^RQ\d+\s*[:：]/iu.test(cleaned)
    || /^【[^】]{1,80}】$/u.test(cleaned)
    || cleaned.length <= 80
      && /(?:综合病例分析|病例分析|医学分析|临床分析|核心结论|结论|最可能(?:的)?诊断(?:\s*[与及和]\s*(?:诊断)?依据)?|诊断(?:\s*[与及和]\s*依据)?|鉴别诊断|检查建议|治疗方案|随访计划|参考依据|总体原则)(?:\s*[（(][^）)]*[）)])?$/iu.test(cleaned);
}

const directLabel = /^(?:最终诊断|最可能的?诊断|诊断考虑|诊断|核心判断|初步判断|考虑|推荐)(?:\s*[（(][^）)]*[）)])?\s*[:：]\s*(.*)$/iu;
const explanatoryOpening = /^(?:免责声明|安全提示|注意事项|重要说明|说明|提示|以下(?:内容|分析)|以下依据|本回答|本结果|仅供参考|依据[:：])/iu;
const coreSection = /^(?:[一二三四五六七八九十]+[、.]\s*)?核心[^：:\n]{0,20}(?:结论|建议|判断|措施|管理)/u;

function nextSubstantiveLine(lines: string[], start: number): string | null {
  for (const raw of lines.slice(start)) {
    const candidate = stripDisplayMarkdown(raw);
    if (!candidate || explanatoryOpening.test(candidate)) continue;
    if (presentationOnlyHeading(raw) || coreSection.test(candidate)) continue;
    return candidate;
  }
  return null;
}

export function directAnswerFrom(answer: string, headline?: string): string {
  const lines = answer.replace(/\r\n/g, "\n").split("\n");
  for (const [index, raw] of lines.entries()) {
    const candidate = stripDisplayMarkdown(raw);
    const match = directLabel.exec(candidate);
    if (!match) continue;
    const inline = match[1].trim();
    if (inline) return inline;
    const following = nextSubstantiveLine(lines, index + 1);
    if (following) return following;
  }

  for (const [index, raw] of lines.entries()) {
    if (!coreSection.test(stripDisplayMarkdown(raw))) continue;
    const following = nextSubstantiveLine(lines, index + 1);
    if (following) return following;
  }

  const explicitHeadline = stripDisplayMarkdown(headline || "");
  if (
    explicitHeadline
    && !explanatoryOpening.test(explicitHeadline)
    && !presentationOnlyHeading(explicitHeadline)
  ) return explicitHeadline;

  for (const raw of lines) {
    const candidate = stripDisplayMarkdown(raw);
    if (!candidate || explanatoryOpening.test(candidate)) continue;
    if (presentationOnlyHeading(raw) || coreSection.test(candidate)) continue;
    return candidate;
  }

  const firstParagraph = paragraphsFrom(answer)[0];
  return stripDisplayMarkdown(firstParagraph || "") || "暂未生成可展示的结论。";
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

function numberValue(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function retrievalSummaryFrom(
  execution: Record<string, unknown>,
  cards: EvidenceCard[],
): RetrievalSummary | null {
  const provided = objectValue(execution.retrieval_summary);
  const retrieval = objectValue(execution.retrieval);
  const admittedIds = stringArray(retrieval.admitted_evidence_ids);
  const hasRetrievalActivity = Boolean(
    typeof retrieval.query === "string" && retrieval.query.trim()
    || typeof retrieval.collection === "string" && retrieval.collection.trim()
    || numberValue(retrieval.retrieved_count) > 0
    || admittedIds.length
    || cards.length,
  );
  if (!Object.keys(provided).length && !hasRetrievalActivity) return null;

  const logical = typeof provided.logical_collection === "string"
    ? provided.logical_collection
    : typeof retrieval.collection === "string" ? retrieval.collection : null;
  const topScoreFromCards = cards.reduce<number | null>(
    (highest, card) => typeof card.score === "number" && (highest === null || card.score > highest) ? card.score : highest,
    null,
  );
  return {
    query: typeof provided.query === "string"
      ? provided.query
      : typeof retrieval.query === "string" ? retrieval.query : null,
    queryCount: numberValue(provided.query_count, Object.keys(retrieval).length ? 1 : 0),
    logicalCollection: logical,
    physicalCollection: typeof provided.physical_collection === "string"
      ? provided.physical_collection
      : logical,
    topK: typeof provided.top_k === "number" ? provided.top_k : null,
    candidateCount: numberValue(provided.candidate_count, numberValue(retrieval.retrieved_count)),
    admittedCount: numberValue(provided.admitted_count, admittedIds.length),
    uniqueEvidenceCount: numberValue(provided.unique_evidence_count, admittedIds.length || cards.length),
    topScore: typeof provided.top_score === "number" ? provided.top_score : topScoreFromCards,
  };
}

function traceFrom(execution: Record<string, unknown>): TraceEvent[] {
  return Array.isArray(execution.trace_events)
    ? execution.trace_events.filter((event): event is TraceEvent => Boolean(event && typeof event === "object"))
    : [];
}

function toolsFrom(execution: Record<string, unknown>, traceEvents: TraceEvent[]): ToolInfo[] {
  const tools = Array.isArray(execution.tools) ? execution.tools : [];
  const explicitTools = tools.map((raw) => {
    const tool = objectValue(raw);
    return {
      name: typeof tool.name === "string" ? tool.name : "tool",
      status: typeof tool.status === "string" ? tool.status : undefined,
    };
  });
  if (explicitTools.length) return explicitTools;

  const tracedTools = new Map<string, ToolInfo>();
  traceEvents.forEach((event) => {
    if (event.event_type !== "tool_call" && event.event_type !== "tool_result" && event.event_type !== "tool_error") return;
    const payload = objectValue(event.payload);
    if (typeof payload.name !== "string") return;
    const result = objectValue(payload.result);
    const failed = event.event_type === "tool_error" || Boolean(payload.error || result.error);
    tracedTools.set(payload.name, {
      name: payload.name,
      status: failed ? "failed" : event.event_type === "tool_call" ? "called" : "completed",
    });
  });
  return [...tracedTools.values()];
}

function memoryFrom(execution: Record<string, unknown>): MemoryInfo | null {
  const memory = objectValue(execution.memory);
  if (!Object.keys(memory).length) return null;
  const historyInjected = stringArray(memory.history_injected);
  const storedFacts = Array.isArray(memory.stored_facts)
    ? memory.stored_facts.map(objectValue).flatMap((fact) => {
      if (typeof fact.key !== "string" || typeof fact.value !== "string") return [];
      return [{ key: fact.key, value: fact.value }];
    })
    : [];
  return {
    sessionId: typeof memory.session_id === "string" ? memory.session_id : "demo-session",
    historyInjected,
    historyInjectedCount:
      typeof memory.history_injected_count === "number"
        ? memory.history_injected_count
        : historyInjected.length,
    storedFacts,
    newSessionHistoryCount:
      typeof memory.new_session_history_count === "number"
        ? memory.new_session_history_count
        : typeof memory.isolated_session_history_count === "number"
          ? memory.isolated_session_history_count
          : 0,
  };
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
  const directSource = source.direct_answer
    ? displayText(source.direct_answer)
    : directAnswerFrom(clinicalDetail, source.headline);
  const plainLanguage = source.plain_language?.trim()
    ? displayText(source.plain_language)
    : plainLanguageFallback;

  return {
    directAnswer: directSource,
    plainLanguage,
    clinicalDetail,
    disclaimer: source.disclaimer || disclaimerFallback,
    evidenceCards,
    evidenceState: evidenceStateFrom(execution, evidenceCards, traceEvents),
    tools: toolsFrom(execution, traceEvents),
    requestItems: requestItemsFrom(execution),
    route: routeFrom(execution),
    workers: workersFrom(execution),
    retrieval: retrievalFrom(execution),
    retrievalSummary: retrievalSummaryFrom(execution, evidenceCards),
    traceEvents,
    memory: memoryFrom(execution),
  };
}
