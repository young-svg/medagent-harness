import type { AnalyzeResponse, EvidenceCard } from "./types";

type DemoCaseFixture = {
  id: string;
  label: string;
  description: string;
  question: string;
  presentation: {
    direct_answer: string;
    plain_language: string;
    clinical_detail: string;
  };
  developer: {
    request_spec: Record<string, unknown>;
    planner: { route: Record<string, unknown>; subtasks: Record<string, unknown>[] };
    workers: Record<string, unknown>[];
    tools: Record<string, unknown>[];
    evidence: {
      status: "NOT_REQUIRED" | "REQUIRED_UNAVAILABLE" | "AVAILABLE";
      required: boolean;
      cards: EvidenceCard[];
      retrieval: Record<string, unknown>;
    };
    trace: Record<string, unknown>[];
    memory?: Record<string, unknown>;
  };
};

const commonTrace = [
  { event_type: "run_start", stage: "lifecycle" },
  { event_type: "request_spec_built", stage: "context" },
  { event_type: "plan_created", stage: "planning" },
  { event_type: "worker_draft", stage: "worker" },
  { event_type: "checker_result", stage: "guardrail" },
  { event_type: "final_answer", stage: "output" },
];

export const demoFixtures: DemoCaseFixture[] = [
  {
    id: "demo-01-simple-single",
    label: "01 · Simple single-agent",
    description: "演示病例：计划接受心脏手术，团队希望快速梳理心肌保护液的常见选择与使用前核实事项。无可识别个人信息。",
    question: "心肌保护通常使用哪类停搏液？请说明常见选择、选择依据和使用前需要核实的事项。",
    presentation: {
      direct_answer: "心肌保护液需按术式、灌注策略、患者情况和本中心方案选择；常见为晶体型、含血型及不同配方的复合停搏液。",
      plain_language: "没有一种停搏液适合所有手术。医生会根据手术时间、给药方式、患者心脏和电解质情况，以及医院成熟流程来决定。",
      clinical_detail: "常见选择包括晶体型与含血型心肌保护方案。选择时需综合预计阻断时间、顺行或逆行灌注方式、再次给药间隔、温度策略及团队经验。使用前应核实适应证、配方与浓度、电解质和肾功能、过敏史、灌注路径及监测和再灌注计划。本演示仅展示信息组织方式，不替代心外科和灌注团队的个体化决策。",
    },
    developer: {
      request_spec: { items: [
        { id: "RQ1", text: "说明常见心肌保护液类别", required: true, semantic_type: "CLINICAL_INFORMATION" },
        { id: "RQ2", text: "说明方案选择依据", required: true, semantic_type: "DECISION_FACTORS" },
        { id: "RQ3", text: "列出使用前核实事项", required: true, semantic_type: "SAFETY_CHECKS" },
      ] },
      planner: {
        route: { mode: "single", reason: "one consultation worker can cover the related items" },
        subtasks: [{ subtask_id: "ST1", assigned_agent: "consultation_agent", description: "整合停搏液类别、选择依据与安全核实项", request_item_ids: ["RQ1", "RQ2", "RQ3"] }],
      },
      workers: [{ worker: "consultation_agent", worker_status: "success", answered_request_item_ids: ["RQ1", "RQ2", "RQ3"] }],
      tools: [],
      evidence: {
        status: "NOT_REQUIRED",
        required: false,
        cards: [],
        retrieval: { query: null, collection: null, retrieved_count: 0, admitted_evidence_ids: [] },
      },
      trace: commonTrace,
    },
  },
  {
    id: "demo-02-multi-agent",
    label: "02 · Multi-agent reasoning",
    description: "演示病例：58岁患者反复餐后胸骨后灼热与反酸，夜间加重，近期出现间歇性吞咽不适。既往超重，无急性胸痛或黑便。",
    question: "请给出最可能诊断及鉴别诊断，说明需要完善的检查，并制定分阶段治疗与安全随访计划。",
    presentation: {
      direct_answer: "首要考虑胃食管反流病，但新出现的吞咽不适属于需要进一步评估的警示线索，应同时排除结构性病变及其他上消化道疾病。",
      plain_language: "症状很像胃酸反流，但吞咽不舒服意味着不能只按普通反流自行处理。下一步要由医生判断是否需要内镜等检查，再根据结果分阶段治疗。",
      clinical_detail: "诊断思路：餐后烧心、反酸及夜间加重支持胃食管反流病；吞咽不适要求评估食管炎、狭窄、裂孔疝、动力障碍及占位性病变。检查路径：先完成病史与用药核对、体格检查和危险分层；存在警示症状时由专科评估上消化道内镜，必要时再考虑反流监测或动力学检查。治疗原则：生活方式干预与规范抑酸治疗并行，根据反应和检查结果调整；避免长期无评估自行用药。若出现进行性吞咽困难、消化道出血、体重明显下降或持续胸痛，应及时就医。",
    },
    developer: {
      request_spec: { items: [
        { id: "RQ1", text: "给出最可能诊断与依据", required: true, semantic_type: "DIAGNOSIS_WITH_BASIS" },
        { id: "RQ2", text: "列出关键鉴别诊断", required: true, semantic_type: "DIFFERENTIAL_DIAGNOSIS" },
        { id: "RQ3", text: "提出检查路径", required: true, semantic_type: "INVESTIGATION_PLAN" },
        { id: "RQ4", text: "制定治疗与安全随访计划", required: true, semantic_type: "TREATMENT_PLAN" },
      ] },
      planner: {
        route: { mode: "multi", reason: "diagnostic and management workstreams are independently assignable" },
        subtasks: [
          { subtask_id: "ST1", assigned_agent: "diagnostic_agent", description: "完成诊断、鉴别诊断与检查分层", request_item_ids: ["RQ1", "RQ2", "RQ3"] },
          { subtask_id: "ST2", assigned_agent: "consultation_agent", description: "形成分阶段治疗与安全随访计划", request_item_ids: ["RQ4"] },
        ],
      },
      workers: [
        { worker: "diagnostic_agent", worker_status: "success", answered_request_item_ids: ["RQ1", "RQ2", "RQ3"] },
        { worker: "consultation_agent", worker_status: "success", answered_request_item_ids: ["RQ4"] },
      ],
      tools: [],
      evidence: {
        status: "NOT_REQUIRED",
        required: false,
        cards: [],
        retrieval: { query: null, collection: null, retrieved_count: 0, admitted_evidence_ids: [] },
      },
      trace: commonTrace,
    },
  },
  {
    id: "demo-03-guideline-rag",
    label: "03 · Guideline / RAG",
    description: "演示病例：成人反复出现血压升高读数，尚未完成标准化诊室外复测，也未系统评估心血管总体风险。",
    question: "请结合临床指南，说明如何确认高血压诊断、进行初始风险评估并安排随访。",
    presentation: {
      direct_answer: "应先用规范诊室测量并结合家庭或动态血压监测确认持续升高，再完成总体风险、靶器官影响和继发因素评估。",
      plain_language: "一次读数高并不等于已经确诊。通常需要用正确方法重复测量，必要时做家庭或24小时监测，同时检查是否已有心、脑、肾等风险。",
      clinical_detail: "演示工作流将问题拆为诊断确认、初始评估和随访三部分，并展示 Research Agent 调用 clinical_guideline 后生成 Evidence Card。下方证据为明确标注的合成 demo fixture，不是真实指南来源，也不得用于临床决策。实际应用必须接入并核验权威指南原文、版本、适用人群和更新日期。",
    },
    developer: {
      request_spec: { items: [
        { id: "RQ1", text: "基于指南说明诊断确认路径", required: true, semantic_type: "GUIDELINE_DIAGNOSIS" },
        { id: "RQ2", text: "说明初始风险与靶器官评估", required: true, semantic_type: "RISK_ASSESSMENT" },
        { id: "RQ3", text: "提出随访安排", required: true, semantic_type: "FOLLOW_UP" },
      ] },
      planner: {
        route: { mode: "single", reason: "guideline-focused evidence synthesis" },
        subtasks: [{ subtask_id: "ST1", assigned_agent: "research_agent", description: "检索并组织指南证据用于诊断、风险评估和随访", request_item_ids: ["RQ1", "RQ2", "RQ3"] }],
      },
      workers: [{ worker: "research_agent", worker_status: "success", answered_request_item_ids: ["RQ1", "RQ2", "RQ3"] }],
      tools: [{ name: "clinical_guideline", status: "success", query: "demo hypertension diagnosis risk assessment follow-up" }],
      evidence: {
        status: "AVAILABLE",
        required: true,
        cards: [{
          evidence_id: "DEMO-EV-001",
          title: "Synthetic guideline evidence — demonstration only",
          source: "DEMO FIXTURE — not a real medical source",
          section: "Synthetic section: diagnostic confirmation",
          score: 1,
          text_preview: "演示数据：用于验证 source、section 与 preview 的前端展示。不得作为真实医学指南或临床依据。",
        }],
        retrieval: { query: "demo hypertension diagnosis risk assessment follow-up", collection: "demo_fixture_only", retrieved_count: 1, admitted_evidence_ids: ["DEMO-EV-001"] },
      },
      trace: [
        ...commonTrace.slice(0, 3),
        { event_type: "tool_call", stage: "tool", payload: { name: "clinical_guideline" } },
        { event_type: "tool_result", stage: "tool", payload: { name: "clinical_guideline", result: { count: 1 } } },
        ...commonTrace.slice(3),
      ],
    },
  },
  {
    id: "demo-04-memory-followup",
    label: "04 · Multi-turn memory",
    description: "第一轮（同一 demo session）：患者因反复偏头痛咨询，既往记录提示每月约6次发作，伴畏光、恶心，无新发神经功能缺损。\n\n第二轮：用户继续追问预防管理方案。",
    question: "结合刚才信息进一步分析：应该如何记录诱因、评估预防治疗需要，并安排复诊？",
    presentation: {
      direct_answer: "结合上一轮每月约6次发作的信息，应建立头痛日记并由医生评估预防治疗适应证，同时设定疗效与安全性复诊节点。",
      plain_language: "系统会把同一会话中刚才提到的发作频率带入本轮。接下来可记录发作日期、持续时间、诱因和用药效果，再和医生讨论是否需要预防方案。",
      clinical_detail: "本轮演示显示 session-scoped memory：上一轮的症状和发作频率被注入当前分析，而一个全新的 session 保持空历史。管理上可用头痛日记记录频率、持续时间、伴随症状、可能诱因、急性用药和缓解情况；结合功能影响与急性药物使用频率评估是否讨论预防治疗。复诊时比较基线与干预后的头痛日数、严重程度、用药次数和不良反应。若出现突发剧烈头痛、新发神经缺损、发热伴颈强直等警示表现，应立即就医。",
    },
    developer: {
      request_spec: { items: [
        { id: "RQ1", text: "利用上一轮信息制定头痛记录方案", required: true, semantic_type: "MEMORY_GROUNDED_PLAN" },
        { id: "RQ2", text: "评估预防治疗讨论条件", required: true, semantic_type: "TREATMENT_ASSESSMENT" },
        { id: "RQ3", text: "安排复诊与安全提示", required: true, semantic_type: "FOLLOW_UP" },
      ] },
      planner: {
        route: { mode: "single", reason: "follow-up request uses session-scoped clinical context" },
        subtasks: [{ subtask_id: "ST1", assigned_agent: "consultation_agent", description: "结合已注入会话历史完成随访建议", request_item_ids: ["RQ1", "RQ2", "RQ3"] }],
      },
      workers: [{ worker: "consultation_agent", worker_status: "success", answered_request_item_ids: ["RQ1", "RQ2", "RQ3"] }],
      tools: [],
      evidence: {
        status: "NOT_REQUIRED",
        required: false,
        cards: [],
        retrieval: { query: null, collection: null, retrieved_count: 0, admitted_evidence_ids: [] },
      },
      trace: [
        { event_type: "run_start", stage: "lifecycle" },
        { event_type: "memory_read", stage: "context", payload: { session_id: "demo-memory-session", history_count: 2 } },
        ...commonTrace.slice(1),
      ],
      memory: {
        session_id: "demo-memory-session",
        history_injected: [
          "Round 1 · User: 每月约6次偏头痛，伴畏光、恶心。",
          "Round 1 · Assistant: 已记录发作频率与伴随症状，下一轮可继续讨论预防管理。",
        ],
        isolated_session_history_count: 0,
      },
    },
  },
];

function toAnalyzeResponse(fixture: DemoCaseFixture): AnalyzeResponse {
  const { developer, presentation } = fixture;
  return {
    final_answer: presentation.clinical_detail,
    status: "completed",
    missing_required_deliverables: [],
    successful_workers: developer.workers.length,
    failed_workers: 0,
    run_id: fixture.id,
    trace: { status: "completed", fixture: true },
    presentation: {
      ...presentation,
      disclaimer: "Showcase demo fixture only. This is not a real patient record or medical recommendation.",
      evidence_cards: developer.evidence.cards,
      execution_summary: {
        request_spec: developer.request_spec,
        plan: { subtasks: developer.planner.subtasks },
        route: developer.planner.route,
        workers: developer.workers,
        tools: developer.tools,
        complexity_profile: { requires_external_evidence: developer.evidence.required },
        retrieval: developer.evidence.retrieval,
        trace_events: developer.trace,
        memory: developer.memory,
      },
    },
  };
}

export const demoCases = demoFixtures.map((fixture) => ({
  ...fixture,
  response: toAnalyzeResponse(fixture),
}));
