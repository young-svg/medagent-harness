# MedAgent Harness Demo Cases

## Purpose

The four deterministic fixtures demonstrate Agent Harness behavior through realistic synthetic clinical workflows—not isolated medical trivia. Each case contains clinical context, an explicit user task, RequestSpec items, routing and worker ownership, observable execution state, and a three-tier presentation answer.

Selecting a fixture from **Demo Cases** loads local data through the existing presentation adapter. It does not call `/api/analyze`, an LLM, retrieval, or the production runtime. All cases are synthetic and are not medical advice.

## 1. 临床快速分析（Single Agent）

### 背景

58岁男性，半年反复餐后胃灼热与反酸，存在超重和晚餐过晚等风险因素，但没有吞咽困难、出血、贫血或体重下降等警示线索。

### 用户任务

判断最可能诊断和依据，说明需要关注的检查，并给出初步处理原则。

### 展示能力

- RequestSpec 将完整任务拆为诊断、依据、检查和治疗四项。
- Complexity-aware routing 判断四项高度相关且复杂度适中，选择 `single`。
- Consultation Agent 一次覆盖 RQ1–RQ4。
- 外部证据不属于本次请求，Tool / Evidence 显示 `NOT_REQUIRED`。

### 执行流程

Question → RequestSpec → Planner (`single`) → Consultation Agent → Coverage check → Final

Fixture: `examples/demo_cases/demo_01_simple_single.json`

## 2. 复杂病例协作分析（Multi Agent）

### 背景

58岁男性在长期反流症状基础上出现进行性吞咽困难、非主动体重下降、贫血和粪便隐血阳性，需要同时处理高风险诊断、检查优先级及后续管理。

### 用户任务

完成最可能诊断、鉴别诊断、进一步检查方案、治疗和随访计划。

### 展示能力

- RequestSpec 把复杂请求拆为 RQ1–RQ4。
- Planner 识别诊断工作流和治疗管理工作流可独立分工，选择 `multi`。
- Diagnostic Agent 负责 RQ1、RQ2、RQ3：诊断、鉴别诊断和检查优先级。
- Consultation Agent 负责 RQ4：治疗、风险处置和随访。
- Worker 卡片明确显示每个 Agent 的 request item ownership。

### 执行流程

Question → RequestSpec → Planner (`multi`) → Diagnostic Agent (RQ1–RQ3) + Consultation Agent (RQ4) → Coverage check → Final

Fixture: `examples/demo_cases/demo_02_multi_agent.json`

## 3. 循证医学分析（RAG）

### 背景

67岁女性在全膝关节置换术后需要制定静脉血栓预防、出血监测和随访方案。

### 用户任务

用户明确要求“结合相关临床指南”制定管理方案并说明指南依据。

### 展示能力

- RequestSpec 包含管理建议和指南依据两个交付项。
- `requires_external_evidence=true` 仅因用户明确要求外部指南而开启；普通医学问题不会自动检索。
- Planner 分配 Research Agent，并调用 `clinical_guideline`。
- Tool 状态为 `Retrieved`，Evidence 状态为 `AVAILABLE`。
- Evidence Card 使用明确标注的 synthetic demo evidence：
  - source: `Demo Clinical Guideline Fixture`
  - section: `Management Recommendation`
  - preview: 明确说明为合成演示数据且不可用于临床。

### 执行流程

Question → RequestSpec → RAG gate → Planner → `clinical_guideline` → Synthetic evidence retrieved → Research Agent → Final

Fixture: `examples/demo_cases/demo_03_guideline_rag.json`

## 4. 连续诊疗分析（Memory）

### 背景

第一轮中，35岁女性报告每月约6次偏头痛，伴恶心和畏光，并询问预防治疗和记录内容。系统保存发作频率、可能诱因和伴随症状。第二轮继续讨论预防方案。

### 用户任务

结合之前的信息，讨论预防治疗选择因素、疗效记录方式和复诊安排。

### 展示能力

- 同一 session 注入两条上一轮历史，不需要用户重复描述。
- RequestSpec 明确标记 memory-grounded assessment、治疗计划和随访任务。
- Consultation Agent 基于注入历史完成纵向管理建议。
- Memory Continuity 显示同一 session 的历史内容；新 session 历史数为 `0`，证明会话隔离。

### 执行流程

Round 1 saved context → Round 2 question → Same-session memory read → RequestSpec → Planner → Consultation Agent → Final

Fixture: `examples/demo_cases/demo_04_memory_followup.json`

## Manual showcase

1. Start the frontend from `web/` with `npm run dev`.
2. Select each entry from **Demo Cases**; no Analyze click is required.
3. Review Direct Answer, Plain Language, and Clinical Detail.
4. Expand **查看 Agent 工作过程** to inspect RequestSpec, route, worker ownership, Tool / Evidence, trace, and Memory Continuity where applicable.
