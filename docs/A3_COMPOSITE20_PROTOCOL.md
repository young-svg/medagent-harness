# MedAgent V1 A3：20 题综合工程评测冻结方案（待执行）

日期：2026-10-29。本轮只有离线预检，**没有发起 A3 真实模型请求**。精确输入、目标、预期能力、逐题通过/失败条件和四份合成检索文档固定在 [A3_COMPOSITE20_CASES.json](A3_COMPOSITE20_CASES.json)。评测开始后不因结果修改题目、文档、评分规则或阈值；如需新版，应另起版本并保留旧结果。该 JSON 在本轮预检后的 SHA-256 为：

    5647fa1c596934e7139377dd0a8791b760e17958cd518d1cd6913c0f18dc343c

评测脚本/人工运行前须再次核对 SHA-256。仓库代码 Commit、模型名、网关、参数、Prompt/Skill 文件哈希、Milvus 建库模型版本与归一化方式、Collection Schema/Metric 和题目哈希均写入评测元数据；API Key、原始 Prompt 与原始 Trace 不入库。

## 20 题目录

以下是可读索引；**JSON 中的 description/question 原文才是冻结输入**，表中摘要不能替代原文。每题 JSON 同时有 goal、expected、pass、fail 字段。

| ID | 类别 | 冻结输入摘要与目标 | 预期能力和通过核心 | 失败核心 |
| --- | --- | --- | --- | --- |
| S01 | Single | 蓝桥、36.7℃；单记录事实提取 | 一个 Worker；准确复述且不诊断 | 多 Worker、漏事实或编造诊断 |
| S02 | Single | 轻度鼻塞、无药物信息；识别缺失 | 一个 Worker；指出信息缺口 | 擅自补全或确定用药 |
| S03 | Single | 虚构药物 DX-1、过敏史未知 | 一个 Worker；保留未知与安全边界 | 赋予 DX-1 真实药理 |
| S04 | Single | 复查时间待定；谨慎摘要 | 一个 Worker；不编造日期 | 给出无来源日期 |
| I01 | 无依赖 Multi | 独立记录甲/乙；分别概括 | 两任务无依赖、执行重叠、各自归属 | 串扰、依赖或漏项 |
| I02 | 无依赖 Multi | 甲红旗字段、乙过敏史 | 并行、分开说明已知/未知 | 把甲字段用于乙 |
| I03 | 无依赖 Multi | P-11 蓝/待核对，Q-22 绿/已核对 | 并行且标签不互换 | 混淆记录 |
| I04 | 无依赖 Multi | 两个独立数据源 3/5 项 | 并行、按题目顺序合成 | 数字或归属错误 |
| D01 | 依赖 Multi | 红旗否 → 观察候选合成规则 | 下游等待并使用上游提取结果 | 无 Handoff 或标签错误 |
| D02 | 依赖 Multi | 红旗是 → 转介候选合成规则 | 自主 depends_on，走另一规则分支 | 提前执行或观察候选 |
| D03 | 依赖 Multi | DX-2、过敏史未知 → 需补充信息 | 保留未知，Handoff 来源可追溯 | 补全未知或给适宜性结论 |
| D04 | 依赖 Multi | X/Y 冲突 → 待人工核对 | 上游比较后下游应用规则 | 忽略冲突或虚报完成 |
| R01 | RAG | 合成轻症咽痛与 SYN-R1 | 自主真实 Milvus 检索、准入、引用和局限 | 不检索或冒充真实指南 |
| R02 | RAG | 合成卡 QF-12 与 SYN-R2 | 精确检索第3天测试规则 | 借用别的卡号或医学化 |
| R03 | RAG | ZETA 两个冲突版本 | 只比较实际准入证据，不强行择日 | 捏造未准入版本或忽略冲突 |
| R04 | RAG | 语料不存在 QX-91 | 检索后识别无匹配证据并拒绝借用 | 把其他卡号规则当 QX-91 |
| M01 | Memory | Session A 首轮：BLUE-ORBIT-17、2天 | 答对并写入 A 历史 | 写入失败或错值 |
| M02 | Memory | Session A 次轮：不重复事实，询问回忆 | Planner/Worker 同一历史，正确回忆 A | 从当前输入伪装回忆 |
| M03 | Memory | Session B 首轮：GREEN-KEEL-29、4天 | B 历史为零且无 A 内容 | A 历史进入 B 输入 |
| M04 | Memory | Session B 次轮：不重复事实，询问回忆 | 仅 B 历史，答 B 且不出现 A | 跨 Session 串扰或漏注入 |

## 冻结的执行与判定口径

1. **运行单元**：20 个 case ID 各运行一次，不自动整题重跑。S/I/D/R 各使用独立 Session；M01→M02 用同一长生命周期 Engine 和 Session A，M03→M04 用同一 Engine 和 Session B。Memory 四轮按给定顺序执行；其余案例只要共用配置但不共用 Session，可按 ID 顺序运行以便审计。
2. **真实路由**：不注入 Planner 计划，不强制工具调用。原样保存 plan_created、route_selected、depends_on、Worker 开始/结束与 Handoff。Single 目标为一个 Worker，I 为两个无依赖且时间重叠，D 为显式依赖及等待；若模型未自主选择预期路由，记录为“路由能力不达标”，不能修改计划让其过关。RAG 必须有实际 Milvus 查询。
3. **三轴评分**：A 轴 Harness：最终 status、失败/阻断、计划/工具/Trace 协议；B 轴结构覆盖：request_spec_built 的原始必需 ID、Worker request_coverage 和 final_request_coverage 分开统计；C 轴语义：按 JSON 每题 pass/fail 原文核查事实、来源、局限和无臆造。单题总 PASS 需 A/B/C 及类别路由、RAG/Memory 专项条件全部通过；否则可记录 PARTIAL/FAIL 和首要原因。completed 或 RQ1=1/1 **不能自动替代语义满足**。
4. **预检的 RequestSpec 粒度**：现有 build_request_spec 对 S/R/M 各生成一个必需 RQ1；I/D 的两个显式编号问题各生成必需 RQ1、RQ2。评测时以实际 request_spec_built 为准，保留完整输出；不得人工把 RQ1 拆分后回写系统结果。语义量表独立检查复合题中的每个要求。
5. **RAG 固定材料**：仅使用 JSON 的 4 份人工合成文档，分别标识 SYN-R1、SYN-R2、SYN-R3A、SYN-R3B；新建仓库外 Milvus Lite DB，不读旧医疗数据库。建库与查询都用缓存的 BAAI/bge-small-zh-v1.5，归一化向量、512 维、COSINE、阈值 0.63。原始分数、准入/过滤、实际 Query、来源 ID 与回答引用逐项记录。R04 即便误准入语义不相关片段，也必须识别 QX-91 不在资料中；检索精度缺陷单列，不能篡改阈值。
6. **Memory 固定材料**：M02/M04 当前输入不复述上一轮独有代号或天数。是否隔离必须根据 memory_read、Planner/Worker 实际输入和 Session ID 判断，不能只凭答案猜测。记录历史预算、估算用量、保留/裁剪条数和写入去重；估算值不是提供商真实 Token。A/B 事实不得交叉注入。
7. **每题记录字段**：case ID、题目哈希、run ID、模型/配置摘要、实际 plan/route、status、Worker 覆盖、最终覆盖、语义逐点结果、真实 HTTP 请求数、各请求 prompt/completion/total tokens（含 Presentation）、总耗时、工具调用及参数、检索 Query/Collection/分数/准入、Handoff 来源、错误类型、失败阶段、Trace 事件链。请求数计数器在 HTTP 请求前占位，基础设施重试和恢复调用计入限额；不能只按成功的 llm_response 计数。
8. **失败分析**：从所有未通过题中按“路由/依赖”“RAG/工具”“Memory/覆盖”类型优先各选首个，最多 3 题；若不足则按 case ID 补足 2 题。只读检查 request_spec_built → plan_created → llm_request/response → tool_call/retrieval_query/retrieval_result/tool_result → worker_handoff → final_request_coverage → presentation_transform → run_end 的父子链，判断模型行为、Harness 行为、证据质量或评分粒度是哪一层失败。只诊断，不改题、不重跑。

## 执行流程、硬上限和预计用量

1. 先核对 case JSON 哈希、Git Commit/工作区、缓存模型和合成 Collection 的建库元数据；运行离线 pytest 与 Ruff。运行产物全部写仓库外；保留旧 A1/A2 Trace，不覆盖。
2. 从本地受控环境加载现有 OpenAI-compatible API 配置，不打印密钥；显式校验真实客户端与真实 Milvus 后端类型。每题开始前检查总剩余额度和本题额度。计数器包裹所有模型调用（Planner、Worker 续接、恢复/重试、Synthesis、Presentation）；达到上限时拒绝**发出**下一次 HTTP 请求。
3. 预期请求数按既有 A1/A2 相近案例估算：Single 每题约 3，I/D 每题约 5，RAG 每题约 7，Memory 每题约 3，合计约 **92 次**。逐题硬上限：S 5、I 7、D 7、R 7、M 5；总硬上限 **124 次**。整题不重跑，异常可标记失败；不为凑齐成功次数追加调用。
4. 同口径粗估总用量约 **13.2 万输入 + 8.3 万输出 ≈ 21.5 万 Token**，不是上限，也不能替代实际 usage。若模型路由、长文本或重试改变，实际消耗可能明显不同；每题与总计均以提供商返回的 usage 为准。
5. **金额预算尚不能可靠给定**：当前 deepseek-flash 走第三方网关，不是官方 api.deepseek.com；第三方实际费率、缓存计费和可能的加价需先从该网关账单确认。金额公式为“输入未命中 Token × 未命中价 + 输入命中 Token × 命中价 + 输出 Token × 输出价”，再加网关适用费用。可参考[官方 DeepSeek 价目页](https://api-docs.deepseek.com/quick_start/pricing/)理解计费项，**不得把官方价目当成该第三方网关的报价**。在金额费率确认前，只能冻结请求与 Token 规划，不能承诺人民币或美元总支出。

   仅作量级演算：若约 13.2 万输入全部按官方 Flash 缓存未命中价、8.3 万输出按官方价计，官方非高峰/高峰分别约 USD 0.07 / 0.14；实际第三方网关费用可能不同，且这不是消费上限。

## 离线预检结论与启动门槛

JSON 可解析、20 个 ID 唯一，五类各 4 题；RequestSpec 实测粒度如上。A2 已证明同一缓存 Embedding 模型与合成 Milvus Lite 路径可用，但 **A3 独立语料库尚未创建，20 题没有运行**。开始付费评测前仍须：确认第三方网关费率/可接受金额；审核本冻结文件与题目哈希；获得明确执行批准；创建并只读校验 A3 独立合成库。A3 方案和题目本轮不提交、不 Push。
