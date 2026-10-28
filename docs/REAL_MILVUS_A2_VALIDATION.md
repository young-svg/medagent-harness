# MedAgent V1 A2：合成 Milvus Lite 检索与真实 LLM 联调

记录日期：2026-10-29。此报告只归档已完成的合成数据实验；原始 Trace、合成数据库、模型凭证均不入库。PASS 是工程链路结论，不是医学有效性或临床部署结论。

## 范围与环境

- 检索后端：真实 MilvusRetrievalBackend / Milvus Lite；本机缓存的 BAAI/bge-small-zh-v1.5 用于建库与查询，向量均归一化；512 维、COSINE、准入阈值 0.63。测试库与既有医疗数据隔离。
- 离线四类复验：ScriptedLLM 控制 Planner/Worker，实际查询 Milvus Lite，不发起模型 API 请求。阈值边界使用专门构造的可控向量，不能当作真实 Embedding 检索质量证据。
- 最终端到端：OpenAICompatibleLLM（第三方网关 deepseek-flash）+ 真实 Milvus Lite，另建独立的合成指南 Collection；指南正文用同一缓存模型生成向量，不复用阈值边界向量。Planner 自主规划，Worker 自主发起工具调用；一次运行，7 次请求硬上限，无重跑。
- 所有病例和文档均为合成测试材料；示例工具 assess_risk 不是外部临床服务。

## 修复前记录（保留历史结论）

| 冻结场景 | 历史结论 | 可核对的观测 |
| --- | --- | --- |
| 有效检索 | PASS | 64be72c4-ae5c-4356-9114-f17db41f00d2；COSINE 0.999 准入、0.45 过滤。 |
| 无相关证据 | PARTIAL | f85d2b79-d1d9-4bd7-b6d3-d93fcf38d37f；原始 0.0 过滤正确，但首次工具结果误报 no_new_relevant_evidence。 |
| 低分/阈值边界 | PASS | 8873fba1-d67c-46d7-8964-691adb0ca85d；0.631 准入、0.629 过滤。 |
| 检索异常 | PARTIAL | 2eca8c9d-206d-4b76-960c-cf59e78005d9；缺失 Collection 报错可见，但无 retrieval_query 事件。 |

另有 ef9af264-f887-4d09-9344-8cad8c9b1c9b 是通用 Collection 路由补充检查，不计入上述四场景的 2 PASS / 2 PARTIAL。

## 最小修复与离线回归

- [agent_loop.py](../medagent/runtime/agent_loop.py)：只有“本次已准入、去重后无新增”才返回 no_new_relevant_evidence；首次无准入证据返回 no_relevant_evidence。
- [native_engine.py](../medagent/runtime/native_engine.py)：后端调用前构造实际 Query、逻辑 Collection 与子任务上下文。后端异常携带该上下文及原始错误类型；Trace 记录 tool_call → retrieval_query → error → tool_result，不伪造 retrieval_result。
- [test_retrieval_trace_failures.py](../tests/test_retrieval_trace_failures.py)：覆盖首次空准入、重复证据及失败检索的 Query/错误因果链。正常检索和最终状态协议不变。

修复后四类独立复验均 **PASS**：

| 场景 | Run ID | 关键结果 |
| --- | --- | --- |
| 有效检索 | b0cd11ff-ffb9-42cb-9dcd-14df2cef7cb5 | 0.999 准入、0.45 过滤；Query 与 Trace 一致。 |
| 无相关证据 | a15c6c7e-bc36-4cbc-acc5-d7800e65cf82 | 0.0 过滤；首次返回 no_relevant_evidence。 |
| 阈值边界 | 49c50511-a1d8-42e0-bae5-3a135a1d9771 | 0.631 准入、0.629 过滤。 |
| 检索异常 | 1217a1bb-bb81-4ae6-adb7-c9971c2e444e | Query、Collection、RetrievalSchemaError 与父子事件保留；无 retrieval_result。 |

四个 Run ID 仅用于本地外置 Trace 定位；仓库不提供原始记录。针对性回归 25 项及全量 pytest 426 项通过，Ruff 和 git diff --check 通过。检索异常本身不会自动把有效的最终回答强制标成失败。

## 真实 LLM + 合成 Milvus Lite：单次端到端

Run ID：abe4f8fc-e735-4666-a644-82398d422b11。合成轻症咽痛病例询问居家观察、警示信号与证据局限；合成指南明确写有“非真实临床指南、不可用于临床决策”。两个独立合成 Collection clinical_guidelines、clinical_knowledge 均为 512 维 COSINE，逻辑名与物理名一一对应；建库与查询使用同一 Embedding 模型及归一化方式。

| 环节 | 实测 |
| --- | --- |
| 自主规划 | ST1 research_agent 检索证据；ST2 consultation_agent 自主声明 depends_on=[ST1]。无计划注入。 |
| Handoff | dependency_wait → worker_handoff → dependency_ready；ST1 的结构化答案携带 RQ1、Worker 和来源 worker_draft 事件 ID；ST2 的模型输入包含上游证据标识。 |
| 工具 | 三次合法 clinical_guideline 调用，原始 COSINE 分数依次为 0.79457、0.79767、0.83844；均满足 0.63。第二次命中同一证据，去重后工具结果为 no_new_relevant_evidence；另有一次示例 assess_risk 调用。 |
| Query / Trace | 三次 retrieval_query 与对应 tool_result.query 完全一致，均含当前子任务、交付项和模型工具输入；实际后端调用使用同一运行时 Query 变量。未单独采集 Milvus 底层 RPC 日志。 |
| 回答与覆盖 | 最终回答引用 milvus-clinical_guidelines-1 及其合成来源，使用片段中的观察条件与警示信号，并说明来源只供演示。status=completed、user_request_complete=true、最终必需 RQ1 覆盖 1/1。 |
| 用量 | Planner 1、Research Worker 2、Consultation Worker 2、Synthesis 1、Presentation 1，共 7/7 次真实请求；12,220 输入 + 6,028 输出 = 18,248 Token；端到端 41.88 秒。无额外恢复调用。 |

## 结论和未验证边界

**A2 合成环境工程联调 PASS，可按该限定范围关闭。** 这证明真实模型自主规划、真实向量检索、准入、结构化 Handoff、回答引用和 Trace 可以连通；不证明真实指南质量或医疗安全。

- 数据库仅含人工合成片段，未接入既有医疗知识库、真实患者资料或真实医疗服务；单条文档不能检验召回率、混合语料排序和来源可靠性。
- 本题的复合要求被 RequestSpec 合并为一个 RQ1（UNKNOWN）；结构覆盖 1/1 不等于各语义细项都被独立校验。回答内容由人工对照冻结要求审阅。
- 模型重复检索相同文档，消耗工具预算；本次没有评测检索效率最优性。一个工具预算用尽的拟议查询未执行，不能将其写成实际检索。
- 检索失败 Trace 的错误原因在 tool_result，错误类型在 error 事件；原始 Trace 可能含完整合成输入、Prompt 和模型文本，不进入 Git。
- 本报告的分数来自合成 COSINE Collection，0.63 的有效性不能外推到其他 Metric、Embedding 或真实语料。
