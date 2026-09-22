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
  storedFacts: Array<{ key: string; value: string }>;
  newSessionHistoryCount: number;
};

export type PresentationView = {
  directAnswer: string;
  plainLanguage: string;
  clinicalDetail: string;
  disclaimer: string;
  evidenceCards: EvidenceCard[];
  evidenceState: EvidenceState;
  tools: ToolInfo[];
  requestItems: RequestItem[];
  route: RouteInfo | null;
  workers: WorkerPlan[];
  retrieval: RetrievalInfo | null;
  traceEvents: TraceEvent[];
  memory: MemoryInfo | null;
};

export type BackendPresentation = {
  direct_answer?: string;
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
