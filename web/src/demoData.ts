import type { AnalyzeResponse, EvidenceCard } from "./types";
import demoFinalAnswers from "./demoFinalAnswers.json";
import demoEvidenceCards from "./demoEvidenceCards.json";

type DemoCaseFixture = {
  id: string;
  label: string;
  question: string;
  case_context: string;
  memory_demo?: {
    first_turn_request: string;
    buffer_summary: string;
  };
  memory_context?: {
    session_id: string;
    history_injected_count: number;
    new_session_history_count: number;
  };
  presentation: {
    direct_answer: string;
    plain_language: string;
    clinical_detail: string;
  };
  developer: {
    request_spec: Record<string, unknown>;
    route: Record<string, unknown>;
    planner: { subtasks: Record<string, unknown>[] };
    workers: Record<string, unknown>[];
    tool_state: { status: string; tools: Record<string, unknown>[] };
    evidence: {
      status: "NOT_REQUIRED" | "REQUIRED_UNAVAILABLE" | "AVAILABLE";
      required: boolean;
      cards: EvidenceCard[];
      retrieval: Record<string, unknown>;
    };
    trace: Record<string, unknown>[];
  };
};

const commonTrace = [
  { event_type: "run_start", stage: "lifecycle", label: "Question received" },
  { event_type: "request_spec_built", stage: "context", label: "RequestSpec extracted" },
  { event_type: "plan_created", stage: "planning", label: "Planner selected route" },
  { event_type: "worker_draft", stage: "worker", label: "Workers completed" },
  { event_type: "checker_result", stage: "guardrail", label: "Coverage checked" },
  { event_type: "final_answer", stage: "output", label: "Final answer generated" },
];

export const demoFixtures: DemoCaseFixture[] = [
  {
    id: "demo-01-simple-single",
    label: "01 · 临床快速分析（Single Agent）",
    case_context: "58岁男性，反复餐后胃灼热、反酸半年，每周约3～4次，平卧及晚餐过晚时加重。BMI 28 kg/m²，偶尔饮酒；无吞咽困难、呕血、黑便或近期体重下降。心电图无急性异常，血常规未见贫血，近期未规律接受抑酸治疗。",
    question: "请分析最可能诊断、诊断依据、需要关注的检查以及初步处理方案。",
    presentation: {
      direct_answer: "最可能诊断：胃食管反流病（GERD）。\n关注：目前没有明确警示症状，可先规范评估与初步处理；若症状持续、加重或出现吞咽困难、出血、体重下降，应及时进一步检查。",
      plain_language: "症状模式很符合胃酸反流：饭后和平躺时更明显，超重和晚餐过晚也可能加重。现有信息没有提示紧急危险，但仍应由医生结合症状频率和治疗反应决定是否需要检查。",
      clinical_detail: demoFinalAnswers["demo-01-simple-single"],
    },
    developer: {
      request_spec: { items: [
        { id: "RQ1", text: "判断最可能诊断", required: true, semantic_type: "DIAGNOSIS" },
        { id: "RQ2", text: "说明诊断依据", required: true, semantic_type: "DIAGNOSIS_WITH_BASIS" },
        { id: "RQ3", text: "提出需要关注的检查", required: true, semantic_type: "INVESTIGATION_PLAN" },
        { id: "RQ4", text: "给出初步治疗原则", required: true, semantic_type: "TREATMENT_PLAN" },
      ] },
      route: { mode: "single", reason: "四个请求项相互依赖、复杂度适中，一个 Consultation Agent 可完整覆盖" },
      planner: {
        subtasks: [{ subtask_id: "ST1", assigned_agent: "consultation_agent", description: "完成诊断判断、检查分层与初步管理建议", request_item_ids: ["RQ1", "RQ2", "RQ3", "RQ4"] }],
      },
      workers: [{ worker: "consultation_agent", worker_status: "success", answered_request_item_ids: ["RQ1", "RQ2", "RQ3", "RQ4"] }],
      tool_state: { status: "NOT_REQUIRED", tools: [] },
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
    label: "02 · 复杂病例协作分析（Multi Agent）",
    case_context: "58岁男性，反酸和胸骨后灼热约1年，近3个月出现进行性固体食物吞咽困难，近2个月非主动减重5 kg。血红蛋白105 g/L，粪便隐血阳性。无呕血，生命体征稳定。尚未接受胃镜检查。",
    question: "请分析最可能诊断和鉴别诊断，制定进一步检查方案，并给出治疗与随访计划。",
    presentation: {
      direct_answer: "最需要优先排除：食管或胃食管结合部占位性病变。\n关注：进行性吞咽困难、非主动体重下降、贫血和粪便隐血阳性均为警示信号，应尽快完成专科评估，而不能只按普通反流处理。",
      plain_language: "虽然患者长期有反流症状，但现在出现了吞咽越来越困难、体重下降和贫血。这些变化要求尽快检查食管和胃，先排除结构性病变，再决定具体治疗。",
      clinical_detail: demoFinalAnswers["demo-02-multi-agent"],
    },
    developer: {
      request_spec: { items: [
        { id: "RQ1", text: "判断最可能诊断及危险程度", required: true, semantic_type: "DIAGNOSIS_WITH_BASIS" },
        { id: "RQ2", text: "列出关键鉴别诊断", required: true, semantic_type: "DIFFERENTIAL_DIAGNOSIS" },
        { id: "RQ3", text: "制定进一步检查方案", required: true, semantic_type: "INVESTIGATION_PLAN" },
        { id: "RQ4", text: "制定治疗和随访计划", required: true, semantic_type: "TREATMENT_PLAN" },
      ] },
      route: { mode: "multi", reason: "警示症状需要独立诊断工作流，且治疗随访可由另一角色并行规划" },
      planner: {
        subtasks: [
          { subtask_id: "ST1", assigned_agent: "diagnostic_agent", description: "完成高风险诊断、鉴别诊断与检查优先级", request_item_ids: ["RQ1", "RQ2", "RQ3"] },
          { subtask_id: "ST2", assigned_agent: "consultation_agent", description: "基于诊断路径制定治疗、风险处置与随访节点", request_item_ids: ["RQ4"] },
        ],
      },
      workers: [
        { worker: "diagnostic_agent", worker_status: "success", answered_request_item_ids: ["RQ1", "RQ2", "RQ3"] },
        { worker: "consultation_agent", worker_status: "success", answered_request_item_ids: ["RQ4"] },
      ],
      tool_state: { status: "NOT_REQUIRED", tools: [] },
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
    label: "03 · 循证医学分析（RAG）",
    case_context: "60岁男性，已诊断原发性高血压，目前血压控制一般，希望优化生活方式管理。",
    question: "请结合相关指南说明高血压患者生活方式管理方案。",
    presentation: {
      direct_answer: "结合已检索到的高血压生活方式资料，重点落实限盐、健康膳食、规律运动、体重管理、戒烟限酒与家庭血压记录，并持续复诊。",
      plain_language: "减少盐分和多运动有助于降低血管里的压力；持续记录家庭血压，才能判断这些改变是否有效，是否还需要医生调整治疗。",
      clinical_detail: demoFinalAnswers["demo-03-guideline-rag"],
    },
    developer: {
      request_spec: { items: [
        { id: "RQ1", text: "说明高血压患者的生活方式管理方案", required: true, semantic_type: "TREATMENT_PLAN" },
        { id: "RQ2", text: "结合已检索资料说明指南依据", required: true, semantic_type: "GUIDELINE_EVIDENCE" },
      ] },
      route: { mode: "multi", reason: "指南证据检索与生活方式管理建议由 Research Agent、Consultation Agent 协作" },
      planner: {
        subtasks: [
          { subtask_id: "ST1", assigned_agent: "research_agent", description: "检索高血压生活方式管理资料", request_item_ids: ["RQ2"] },
          { subtask_id: "ST2", assigned_agent: "consultation_agent", description: "整合证据并提出生活方式管理建议", request_item_ids: ["RQ1"] },
        ],
      },
      workers: [
        { worker: "research_agent", worker_status: "success", answered_request_item_ids: ["RQ2"] },
        { worker: "consultation_agent", worker_status: "success", answered_request_item_ids: ["RQ1"] },
      ],
      tool_state: {
        status: "Retrieved",
        tools: [{ name: "clinical_guideline", status: "Retrieved", query: "高血压患者生活方式管理指南" }],
      },
      evidence: {
        status: "AVAILABLE",
        required: true,
        cards: demoEvidenceCards,
        retrieval: { query: "synthetic lifestyle review workflow", collection: "synthetic_demo_guidelines", retrieved_count: 2, admitted_evidence_ids: demoEvidenceCards.map((card) => card.evidence_id) },
      },
      trace: [
        ...commonTrace.slice(0, 3),
        { event_type: "tool_call", stage: "tool", label: "clinical_guideline called", payload: { name: "clinical_guideline" } },
        { event_type: "tool_result", stage: "tool", label: "Synthetic evidence retrieved", payload: { name: "clinical_guideline", result: { count: 2 } } },
        ...commonTrace.slice(3),
      ],
    },
  },
  {
    id: "demo-04-memory-followup",
    label: "04 · 连续诊疗分析（Memory）",
    case_context: "35岁女性，反复偏头痛，每月约发作6次，伴恶心、畏光，无新发神经功能缺损。",
    question: "结合之前信息，进一步讨论预防治疗方案。",
    memory_demo: {
      first_turn_request: "请分析是否需要预防治疗，并说明后续需要持续关注的信息。",
      buffer_summary: "1 user message + 1 assistant response",
    },
    memory_context: {
      session_id: "demo-memory-session",
      history_injected_count: 2,
      new_session_history_count: 0,
    },
    presentation: {
      direct_answer: "可以进入预防治疗评估：上一轮记录的每月约6次发作及伴随症状提示，应与医生讨论预防治疗的获益、风险和个体化选择。\n关注：需继续记录头痛日数、诱因、急性用药、功能影响和不良反应，才能判断方案是否有效。",
      plain_language: "此前每月约6次发作，已经值得认真评估预防治疗。记录头痛和用药情况，可帮助医生判断是否需要开始治疗，以及方案是否真的有效。",
      clinical_detail: demoFinalAnswers["demo-04-memory-followup"],
    },
    developer: {
      request_spec: { items: [
        { id: "RQ1", text: "结合历史评估预防治疗讨论条件", required: true, semantic_type: "MEMORY_GROUNDED_ASSESSMENT" },
        { id: "RQ2", text: "说明预防方案选择因素", required: true, semantic_type: "TREATMENT_PLAN" },
        { id: "RQ3", text: "制定疗效记录与复诊安排", required: true, semantic_type: "FOLLOW_UP" },
      ] },
      route: { mode: "single", reason: "连续诊疗任务依赖同一 session 历史，由 Consultation Agent 完成纵向管理建议" },
      planner: {
        subtasks: [{ subtask_id: "ST1", assigned_agent: "consultation_agent", description: "读取已注入历史并形成预防治疗、记录和复诊方案", request_item_ids: ["RQ1", "RQ2", "RQ3"] }],
      },
      workers: [{ worker: "consultation_agent", worker_status: "success", answered_request_item_ids: ["RQ1", "RQ2", "RQ3"] }],
      tool_state: { status: "NOT_REQUIRED", tools: [] },
      evidence: {
        status: "NOT_REQUIRED",
        required: false,
        cards: [],
        retrieval: { query: null, collection: null, retrieved_count: 0, admitted_evidence_ids: [] },
      },
      trace: [
        { event_type: "run_start", stage: "lifecycle", label: "Question received" },
        { event_type: "memory_read", stage: "context", label: "Same-session history injected", payload: { session_id: "demo-memory-session", history_count: 2 } },
        ...commonTrace.slice(1),
      ],
    },
  },
];

// Local showcase answers are presentation data and never leave the browser.
const threeLayerDemoPresentation: Record<string, {
  direct_answer_title: string;
  direct_answer_sections: Array<{ request_item_id: string; title: string; items: string[] }>;
  plain_explanation: string[];
}> = {
  "demo-01-simple-single": {
    direct_answer_title: "最可能诊断、依据与初步处理",
    direct_answer_sections: [
      { request_item_id: "RQ1", title: "最可能诊断", items: ["首先考虑胃食管反流病，以典型反流症状为主；仍需由医生结合检查确认。"] },
      { request_item_id: "RQ2", title: "诊断依据", items: ["餐后胃灼热和反酸每周3～4次、平卧和晚餐过晚时加重，且目前没有吞咽困难、出血或体重下降等报警表现。"] },
      { request_item_id: "RQ3", title: "需要关注的检查", items: ["先核对症状与用药史；若规范处理后仍不缓解、反复发作或出现报警征象，进一步评估上消化道内镜等检查。"] },
      { request_item_id: "RQ4", title: "初步处理", items: ["调整晚餐时间、体重和诱发因素；由医生指导规范抑酸治疗，约4～8周复评。"] },
    ],
    plain_explanation: [
      "饭后和躺下时胃灼热、反酸更明显，符合胃内容物反流的常见表现。",
      "减少晚餐过晚和卧位反流的诱因，再观察规范治疗的效果，可以帮助判断问题是否主要来自反流；若效果不好或出现新症状，就需要进一步查找其他原因。",
    ],
  },
  "demo-02-multi-agent": {
    direct_answer_title: "诊断、鉴别与下一步方案",
    direct_answer_sections: [
      { request_item_id: "RQ1", title: "最可能诊断", items: ["高度怀疑食管或食管胃结合部恶性肿瘤，食管腺癌可能性较大；目前尚未做胃镜，必须经活检病理证实。"] },
      { request_item_id: "RQ2", title: "主要鉴别诊断", items: ["还需鉴别食管鳞癌或贲门癌、反流相关良性狭窄、重度食管炎，以及贲门失弛缓症等食管动力障碍。"] },
      { request_item_id: "RQ3", title: "进一步检查", items: ["尽快安排上消化道内镜与多点活检；若病理证实恶性，再完善增强 CT、超声内镜等分期检查，同时评估贫血和营养状况。"] },
      { request_item_id: "RQ4", title: "治疗与随访", items: ["治疗由病理、分期和多学科评估决定，同时处理营养和贫血；明确检查结果回访节点，吞咽明显恶化、无法进食或出血时及时就医。"] },
    ],
    plain_explanation: [
      "普通反流通常会引起烧心和反酸，但固体食物越来越难下咽、近期体重下降，再加上贫血和粪便隐血阳性，说明不能只用普通反流解释。",
      "需要尽快通过胃镜直接观察食管和胃，并从可疑区域取组织检查；只有知道病变性质以及是否扩散，才能决定下一步治疗和复查。",
    ],
  },
  "demo-03-guideline-rag": {
    direct_answer_title: "高血压生活方式管理与证据",
    direct_answer_sections: [
      { request_item_id: "RQ1", title: "生活方式管理", items: ["优先限盐，采用蔬菜水果、全谷物等健康膳食；结合自身情况规律运动、控制体重、戒烟限酒，并持续记录家庭血压。"] },
      { request_item_id: "RQ2", title: "已检索到的依据", items: ["合成证据卡用于演示检索、证据准入与来源标记；它们不是临床指南，不能替代真实来源核验或个体化临床判断。"] },
    ],
    plain_explanation: [
      "盐吃得多，身体更容易留住水分，血管里的压力也会增加；减少盐分、控制体重并规律活动，有助于血压更平稳。",
      "家庭血压记录可以让医生看出改变是否奏效；因为每个人的肾功能、药物和其他疾病不同，不能直接照搬同一套目标。",
    ],
  },
  "demo-04-memory-followup": {
    direct_answer_title: "偏头痛预防治疗与复诊",
    direct_answer_sections: [
      { request_item_id: "RQ1", title: "结合既往情况", items: ["此前每月约6次偏头痛发作，伴恶心、畏光，已达到值得与医生讨论预防治疗的频率。"] },
      { request_item_id: "RQ2", title: "预防方案", items: ["由医生结合合并症、生育计划、用药风险及个人偏好选择预防方案；不要自行开始或调整处方。"] },
      { request_item_id: "RQ3", title: "记录与复诊", items: ["继续记录头痛日数、诱因、急性用药和功能影响，复诊时对比治疗前后变化与不良反应。"] },
    ],
    plain_explanation: [
      "此前记录的每月约六次发作以及恶心、畏光，说明头痛已反复影响生活，值得评估预防治疗。",
      "头痛日记能帮助医生看出发作是否减少、治疗是否带来不适，而不是只凭一次就诊时的印象判断。",
    ],
  },
};

function toAnalyzeResponse(fixture: DemoCaseFixture): AnalyzeResponse {
  const { developer, presentation } = fixture;
  const threeLayer = threeLayerDemoPresentation[fixture.id];
  const clinicalDetail = presentation.clinical_detail;
  const directItems = threeLayer?.direct_answer_sections.flatMap((section) => section.items) || [];
  return {
    final_answer: clinicalDetail,
    status: "completed",
    missing_required_deliverables: [],
    successful_workers: developer.workers.length,
    failed_workers: 0,
    run_id: fixture.id,
    trace: { status: "completed", fixture: true },
    presentation: {
      ...presentation,
      ...threeLayer,
      direct_answer_items: directItems,
      direct_answer: directItems.join("\n") || presentation.direct_answer,
      plain_language: threeLayer?.plain_explanation.join("\n\n") || presentation.plain_language,
      clinical_detail: clinicalDetail,
      disclaimer: "Showcase demo fixture only. This is not a real patient record or medical recommendation.",
      evidence_cards: developer.evidence.cards,
      execution_summary: {
        request_spec: developer.request_spec,
        plan: { subtasks: developer.planner.subtasks },
        route: developer.route,
        workers: developer.workers,
        tools: developer.tool_state.tools,
        complexity_profile: { requires_external_evidence: developer.evidence.required },
        retrieval: developer.evidence.retrieval,
        trace_events: developer.trace,
        memory: fixture.memory_context,
      },
    },
  };
}

export const demoCases = demoFixtures.map((fixture) => ({
  ...fixture,
  description: fixture.case_context,
  response: toAnalyzeResponse(fixture),
}));
