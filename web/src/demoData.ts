import type { AnalyzeResponse } from "./types";

export const demoInput = {
  description: "45岁女性，反复心悸、乏力3个月。血常规提示血红蛋白82 g/L、MCV 68 fL；月经量较多，无胸痛、晕厥或活动性出血表现。",
  question: "请分析最可能诊断、诊断依据、需要排除的问题和下一步处理建议。",
};

export const demoResponse: AnalyzeResponse = {
  final_answer: "最可能诊断为缺铁性贫血，需结合铁代谢检查确认，并进一步评估月经过多的原因。",
  status: "completed",
  missing_required_deliverables: [],
  successful_workers: 2,
  failed_workers: 0,
  run_id: "demo-multi-agent",
  trace: { status: "completed" },
  presentation: {
    direct_answer: "最可能诊断：缺铁性贫血。\n\n下一步建议：完善铁代谢检查确认缺铁，并评估月经过多的原因。",
    plain_language: "目前的化验结果提示身体缺少制造红细胞所需的铁，月经过多可能是长期丢失铁的原因。通常还需要抽血确认铁储备，并检查月经过多为什么发生。",
    clinical_detail: "## 诊断依据\n\n血红蛋白82 g/L提示中度贫血，MCV 68 fL提示小细胞性贫血；结合月经过多，首先考虑慢性失血导致的缺铁性贫血。\n\n## 鉴别诊断\n\n需要与地中海贫血、慢性病性贫血及其他消化道或妇科慢性失血鉴别。\n\n## 检查建议\n\n完善血清铁蛋白、转铁蛋白饱和度、网织红细胞、外周血涂片；结合妇科评估异常子宫出血，必要时评估消化道失血。\n\n## 治疗原则\n\n确认缺铁后补铁，同时处理失血病因；根据症状、血红蛋白变化和口服铁耐受性决定补铁途径。\n\n## 安全提示\n\n如出现胸痛、晕厥、静息气促、心悸明显加重或活动性出血，应立即就医。",
    disclaimer: "本示例仅用于展示产品交互，不构成医疗建议。",
    evidence_cards: [],
    execution_summary: {
      request_spec: { items: [
        { id: "RQ1", text: "分析最可能诊断及诊断依据", required: true, semantic_type: "DIAGNOSIS_WITH_BASIS" },
        { id: "RQ2", text: "说明需要排除的问题", required: true, semantic_type: "DIFFERENTIAL_DIAGNOSIS" },
        { id: "RQ3", text: "提出下一步处理建议", required: true, semantic_type: "TREATMENT_PLAN" },
      ] },
      route: { mode: "multi", reason: "multiple_subtasks" },
      plan: { subtasks: [
        { subtask_id: "ST1", assigned_agent: "diagnostic_agent", description: "分析贫血类型、诊断依据和鉴别诊断", request_item_ids: ["RQ1", "RQ2"] },
        { subtask_id: "ST2", assigned_agent: "consultation_agent", description: "形成检查与治疗建议并补充安全提示", request_item_ids: ["RQ3"] },
      ] },
      workers: [
        { worker: "diagnostic_agent", worker_status: "success" },
        { worker: "consultation_agent", worker_status: "success" },
      ],
      retrieval: { query: null, collection: null, retrieved_count: 0, admitted_evidence_ids: [] },
      trace_events: [
        { event_type: "run_start", stage: "lifecycle" },
        { event_type: "request_spec_built", stage: "context" },
        { event_type: "plan_created", stage: "planning" },
        { event_type: "worker_draft", stage: "worker" },
        { event_type: "checker_result", stage: "guardrail" },
        { event_type: "final_answer", stage: "output" },
      ],
    },
  },
};
