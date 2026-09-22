import type { AnalyzeResponse, EvidenceCard } from "./types";

type DemoCaseFixture = {
  id: string;
  label: string;
  question: string;
  case_context: string;
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
    memory?: Record<string, unknown>;
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
      clinical_detail: "诊断判断\n餐后胃灼热、反酸、平卧加重及相关生活方式因素支持胃食管反流病；现有心电图和血常规信息降低了部分替代诊断的可能性，但不能替代完整临床评估。\n\n检查建议\n先核对症状模式、用药史和警示症状。无警示线索时可根据临床评估先行规范处理；疗效不佳、诊断不确定或出现警示表现时，再由医生评估上消化道内镜或反流监测。\n\n治疗原则\n调整进餐时间、体重与诱发因素，并在医生指导下进行规范抑酸治疗和疗效复评。出现进行性吞咽困难、消化道出血、持续胸痛或体重下降时应及时就医。",
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
      clinical_detail: "诊断与鉴别\n占位性病变需要优先排除；同时考虑重度反流性食管炎、消化性狭窄、食管动力障碍以及其他上消化道出血来源。\n\n检查路径\n建议尽快转诊消化专科，评估上消化道内镜及必要的组织学检查；同步复核血常规、铁代谢和出血风险。后续影像、动力学或反流监测应根据内镜结果和专科判断选择。\n\n治疗与随访\n在明确病因前避免仅以经验性抑酸替代检查。治疗按病理和分期结果决定；同时处理贫血、营养和症状风险。建立检查结果回访节点，若吞咽迅速恶化、无法进食、呕血或黑便，应立即就医。",
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
    case_context: "67岁女性，全膝关节置换术后第1天，生命体征稳定，已开始床旁活动。既往无静脉血栓史，肾功能稳定，无活动性出血；术区引流量在团队预期范围内。临床团队需要制定围术期静脉血栓预防与监测方案。",
    question: "请结合相关临床指南，制定术后静脉血栓预防、出血风险监测和随访方案，并说明指南依据。",
    presentation: {
      direct_answer: "管理重点：先完成血栓与出血风险评估，再组合早期活动、机械预防和个体化药物预防，并设置出血及血栓警示监测。\n关注：本案例的证据为 synthetic demo evidence，只用于展示 RAG gating 与 Evidence Card，不能作为真实临床指南。",
      plain_language: "术后既要预防血栓，也要避免增加出血风险。团队会根据手术、活动能力、肾功能和出血情况决定预防方式，并持续观察腿部肿痛、呼吸困难或异常出血。",
      clinical_detail: "管理框架\n先记录静脉血栓与出血风险，再按本中心流程安排早期活动和机械预防；是否使用药物、选择何种方案以及持续时间，必须由临床团队结合肾功能、麻醉方式、伤口与出血状态决定。\n\n监测与随访\n动态观察伤口、引流、血红蛋白和药物相关风险，同时告知单侧下肢肿痛、突发胸痛或呼吸困难等警示症状。出院前明确依从性、活动计划、用药核对和复诊节点。\n\n证据说明\n本回答中的 Evidence Card 是明确标注的合成展示数据。实际临床应用必须连接、核验并引用权威指南原文及最新版本。",
    },
    developer: {
      request_spec: { items: [
        { id: "RQ1", text: "制定术后治疗管理与风险监测建议", required: true, semantic_type: "TREATMENT_PLAN" },
        { id: "RQ2", text: "提供并标明指南依据", required: true, semantic_type: "GUIDELINE_EVIDENCE" },
      ] },
      route: { mode: "single", reason: "用户明确要求指南依据，RAG gate 开启并交由 Research Agent 完成证据整合" },
      planner: {
        subtasks: [{ subtask_id: "ST1", assigned_agent: "research_agent", description: "调用指南能力并组织管理建议与证据说明", request_item_ids: ["RQ1", "RQ2"] }],
      },
      workers: [{ worker: "research_agent", worker_status: "success", answered_request_item_ids: ["RQ1", "RQ2"] }],
      tool_state: {
        status: "Retrieved",
        tools: [{ name: "clinical_guideline", status: "Retrieved", query: "demo postoperative VTE prevention and bleeding monitoring" }],
      },
      evidence: {
        status: "AVAILABLE",
        required: true,
        cards: [{
          evidence_id: "DEMO-GUIDELINE-001",
          title: "Synthetic demo evidence — showcase only",
          source: "Demo Clinical Guideline Fixture",
          section: "Management Recommendation",
          score: 1,
          text_preview: "Synthetic demo evidence: assess VTE and bleeding risk, combine early mobilisation and mechanical prevention, and individualise medication and follow-up under the surgical team's protocol. Not for clinical use.",
        }],
        retrieval: { query: "demo postoperative VTE prevention and bleeding monitoring", collection: "synthetic_demo_evidence", retrieved_count: 1, admitted_evidence_ids: ["DEMO-GUIDELINE-001"] },
      },
      trace: [
        ...commonTrace.slice(0, 3),
        { event_type: "tool_call", stage: "tool", label: "clinical_guideline called", payload: { name: "clinical_guideline" } },
        { event_type: "tool_result", stage: "tool", label: "Synthetic evidence retrieved", payload: { name: "clinical_guideline", result: { count: 1, synthetic: true } } },
        ...commonTrace.slice(3),
      ],
    },
  },
  {
    id: "demo-04-memory-followup",
    label: "04 · 连续诊疗分析（Memory）",
    case_context: "第一轮（同一 demo session）：35岁女性，反复偏头痛，每月约发作6次，常伴恶心和畏光，无新发神经功能缺损。用户询问是否需要预防治疗以及需要记录哪些信息。系统保存了发作频率、可能诱因和伴随症状。\n\n第二轮：用户基于上一轮信息继续讨论预防治疗方案。",
    question: "结合之前的信息，进一步讨论预防治疗方案、疗效记录方式和复诊安排。",
    presentation: {
      direct_answer: "可以进入预防治疗评估：上一轮记录的每月约6次发作及伴随症状提示，应与医生讨论预防治疗的获益、风险和个体化选择。\n关注：需继续记录头痛日数、诱因、急性用药、功能影响和不良反应，才能判断方案是否有效。",
      plain_language: "系统记住了上一轮提到的发作频率、恶心和畏光，因此本轮不用重复输入。接下来应通过头痛日记比较治疗前后的变化，再与医生共同决定是否开始或调整预防方案。",
      clinical_detail: "会话信息使用\n同一 session 注入了上一轮的每月6次发作、恶心、畏光及需要记录诱因的信息；全新 session 的历史计数为0，不会共享这些内容。\n\n预防治疗讨论\n结合发作频率、功能影响、急性药物使用、合并症、生育计划及个人偏好，由医生评估是否开始预防治疗并选择方案。此演示不指定具体处方。\n\n记录与复诊\n头痛日记应记录头痛日数、持续时间、严重程度、诱因、伴随症状、急性用药与效果。复诊时比较基线和治疗后的变化及不良反应；若出现突发剧烈头痛、新发神经缺损、发热伴颈强直等警示表现，应立即就医。",
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
      memory: {
        session_id: "demo-memory-session",
        history_injected: [
          "Round 1 · User: 35岁女性，每月约6次偏头痛，伴恶心、畏光；询问预防治疗和记录内容。",
          "Round 1 · Assistant memory: 已保存发作频率、可能诱因与伴随症状。",
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
        route: developer.route,
        workers: developer.workers,
        tools: developer.tool_state.tools,
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
  description: fixture.case_context,
  response: toAnalyzeResponse(fixture),
}));
