export type EvidenceCard = {
  evidence_id: string;
  title?: string | null;
  source: string;
  section?: string | null;
  score: number | null;
  text_preview: string;
};

export type RequestItem = {
  id: string;
  text: string;
  required: boolean;
  semanticType?: string | null;
};

export type WorkerPlan = {
  id: string;
  name: string;
  description: string;
  requestItemIds: string[];
  status?: string;
};

export type RouteInfo = {
  mode: "single" | "multi" | string;
  reason?: string;
};

export type RetrievalInfo = {
  query?: string | null;
  collection?: string | null;
  retrievedCount: number;
  admittedEvidenceIds: string[];
};

export type RetrievalSummary = {
  query: string | null;
  queryCount: number;
  logicalCollection: string | null;
  physicalCollection: string | null;
  topK: number | null;
  candidateCount: number;
  admittedCount: number;
  uniqueEvidenceCount: number;
  topScore: number | null;
};

export type EvidenceState = {
  status: "NOT_REQUIRED" | "REQUIRED_UNAVAILABLE" | "AVAILABLE";
  reason?: string;
};

export type ToolInfo = {
  name: string;
  status?: string;
};

export type TraceEvent = {
  event_type?: string;
  stage?: string;
  payload?: Record<string, unknown>;
};

export type MemoryInfo = {
  sessionId: string;
  historyInjected: string[];
  historyInjectedCount: number;
  newSessionHistoryCount: number;
};

export type PresentationView = {
  directAnswer: string;
  directAnswerTitle: string;
  directAnswerItems: string[];
  directAnswerSections: Array<{ requestItemId: string; title: string; items: string[] }>;
  plainLanguage: string;
  plainExplanation: string[];
  clinicalDetail: string;
  disclaimer: string;
  evidenceCards: EvidenceCard[];
  evidenceState: EvidenceState;
  tools: ToolInfo[];
  requestItems: RequestItem[];
  route: RouteInfo | null;
  workers: WorkerPlan[];
  retrieval: RetrievalInfo | null;
  retrievalSummary?: RetrievalSummary | null;
  traceEvents: TraceEvent[];
  memory: MemoryInfo | null;
};

export type BackendPresentation = {
  direct_answer?: string;
  direct_answer_title?: string;
  direct_answer_items?: string[];
  direct_answer_sections?: Array<{ request_item_id: string; title: string; items: string[] }>;
  plain_explanation?: string[];
  plain_language?: string;
  clinical_detail?: string;
  headline?: string;
  plain_language_summary?: string;
  professional_answer?: string;
  evidence_cards?: EvidenceCard[];
  execution_summary?: Record<string, unknown>;
  disclaimer?: string;
};

export type AnalyzeResponse = {
  final_answer: string;
  status: string;
  missing_required_deliverables: string[];
  successful_workers: number;
  failed_workers: number;
  run_id: string;
  trace: Record<string, unknown>;
  presentation?: BackendPresentation;
};
