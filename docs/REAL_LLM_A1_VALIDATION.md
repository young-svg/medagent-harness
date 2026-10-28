# MedAgent V1 A1：真实 LLM 端到端联调最终报告

> 记录日期：2026-10-29。范围是冻结的合成案例和已完成的运行；本报告没有发起新的 API 请求。以下 `completed` 和 Coverage 是系统结构化完成状态，不代表医学事实正确性或临床安全性已获验证。

## 结论与证据口径

A1 的**有限范围真实模型联调可关闭**：单 Worker、无依赖并行、Tool Calling、真实 LLM + Fake Retrieval、同 Engine 三轮 Session Memory 的预定检查通过；依赖 Handoff 为 **PARTIAL**。真实模型自主生成了 `depends_on`，运行时完成等待、来源可追踪的 Handoff 与下游使用，但原问题被确定性 RequestSpec 解析为单一 `RQ1`，无法证明诊断、后续建议分别受到独立条目覆盖约束。没有发现阻断这些冻结案例执行的工程故障。

数据口径：每次模型请求包括 Planner、Worker、可选 Synthesis、Presentation，以及工具后的 Worker 续接；Token 来自 Trace 的模型 `usage`，含 Presentation。耗时取各运行 `run_end.latency_ms`（四舍五入），不是各 LLM latency 之和。主验收共 **33 次真实模型请求、44,974 输入 + 24,949 输出 = 69,923 Token，运行耗时合计约 180.1 秒**。不同实验的合计耗时不是一次连续端到端延迟。模型为现有 DeepSeek OpenAI-compatible 接入；真实模型调用不等于真实检索或临床验证。

| 冻结场景 / 证据类别 | 结论 | 实际调用与 Token（输入/输出/合计） | 耗时 | 最终状态与 Coverage | 关键 Trace / 观察 |
| --- | --- | --- | --- | --- | --- |
| 单 Worker / 真实 LLM，无检索 | PASS | 3；2,617 / 852 / 3,469 | 11.29 s | `completed`，必需项 1/1，缺失 0 | `127f51bf-0f80-476a-8f41-379f9707b1fe`：Planner 1、diagnostic Worker 1、Presentation 1；Worker `direct_json`。 |
| 无依赖双 Worker / 真实 LLM，无检索 | PASS | 5；7,370 / 6,270 / 13,640 | 34.68 s | `completed`，2/2，缺失 0 | `aa08d5ee-7fa6-4c8a-9f24-a9f786335206`：Planner 自主产生两个无依赖任务；两个 Worker 请求在约 1 ms 内相继发出、响应时间重叠；Synthesis 1、Presentation 1。任务答案按归属汇总，合成后最终覆盖 2/2；合成病例信息未见交叉归属。 |
| 依赖双 Worker / 真实 LLM，无检索 | **PARTIAL** | 5；8,081 / 6,704 / 14,785 | 48.53 s | `completed`，但仅 **1/1**（`RQ1`） | `2ac95e9c-8fa6-4272-b6c4-df257a873ce1`：Planner 自主产生 `T2.depends_on=[T1]`；`dependency_wait → worker_handoff → dependency_ready`；Handoff 的 `source_event_id` 对应 T1 `worker_draft`，T2 请求晚于 Handoff，实际输入含上游答案。两个 Worker 各成功，Synthesis 1、Presentation 1。 |
| Tool Calling / 真实 LLM，示例工具 | PASS | 4；5,315 / 3,185 / 8,500 | 23.06 s | `completed`，1/1，缺失 0 | `84461fcc-87f5-4421-a21a-6c28ef245a90`：模型主动调用 `analyze_symptoms`、`assess_risk`，各有 `tool_call/tool_result`；参数通过当前校验、Handler 返回示例结果，Worker 在续接后完成。不是外部临床服务验证。 |
| Fake RAG / **真实 LLM + 确定性假后端** | PASS（仅该组合） | 7；13,250 / 5,093 / 18,343 | 37.79 s | `completed`，1/1，缺失 0 | `b52de2be-91c7-4e8d-b6ee-9a5ad59875eb`：真实 Planner 自主安排 research→consultation，模型主动发起 3 次检索；后端 Query 与 `retrieval_query` Trace 对照一致。每次固定返回两条合成候选：0.92 准入、0.45 过滤；Worker 使用准入证据并说明来源/局限，未使用过滤证据。后端不是 Milvus。 |
| Memory / 真实 LLM，同 Engine 三轮 | PASS（正常预算范围） | 9；8,341 / 2,845 / 11,186 | 24.74 s | 三轮均 `completed`、各 1/1 | A1 `ecc3f32b-ad1d-4645-bd70-a881b2fd3e42`、A2 `8fd952e5-0a17-4b30-a6d2-84cd617baeba`、B1 `fd7f0f1e-f8b8-4def-99c1-6ce0a693cce3`；每轮 Planner/Worker/Presentation 各 1 次。 |

上述表格仅计入切换到现有 DeepSeek 兼容配置后的主验收运行。更早的一次无依赖双 Worker 网关运行（`4bd07532-1e8d-4ebf-a6b6-f6ab28986289`）另有 6 次请求、13,913 Token、172.24 秒及一次 `worker_infrastructure_retry_scheduled`，最终仍为 `completed`、2/2；它是历史接入诊断，**不混入主验收合计或冒充无重试结果**。

### 依赖场景为何只能判 PARTIAL

原问题以“先分析……；然后仅根据前一部分……”组织在同一段中，没有两个编号项、列表项，或每行均以问号结束的多行问题。[`build_request_spec()`](../medagent/context/request_spec.py) 只在这些明确边界上拆分；否则保留整个问题为一个 `RQ1`，并不按分号或医学语义推断拆项。实际 `request_spec_built` 正是一个必需 `RQ1`、`semantic_type=UNKNOWN`。Planner 的两个任务及依赖、调度和 Handoff 均正常；然而两部分都归属 `RQ1`，最终 `1/1` 只能证明这个合并条目有非空答案，不能分别证明诊断与条件性建议完整。此局限属于覆盖粒度，不应将 `completed` 解释为两项独立验收通过；本轮不修改解析器或历史结论。

### Memory 与 Fake RAG 的具体边界

三轮 Memory 使用同一 Engine。Session A 第 1 轮写入 2 条；A 第 2 轮当前输入未重复提供独有代号及事实，`memory_read` 注入 A 的 2 条历史，Planner 与 Worker 的实际输入均含同一份裁剪后历史，回答正确回忆。Session B 用与 A 第 2 轮相同的提问，但 `memory_read` 保留 0 条，Planner、Worker 输入和 Trace 均没有 A 的独有事实，回答未知。隔离判断依据**实际输入和 Trace**，而不是只看 B 的回答。三轮各写入 2 条；预算为 4,096 **估算** Token，A 第 2 轮估算历史用量 519、裁剪 0。历史未达到预算，也没有同 Session 重复写入，因此真实联调**未触发裁剪或去重边界**；这些行为仅有离线测试证据。估算值不是模型真实 Token 硬上限。

Fake RAG 的三次 `retrieval_query/retrieval_result/tool_result` 均可对应；相同两条合成候选在三次检索中重复返回。三个 Query 文本并非完全相同，但围绕同一固定证据重复检索，说明此案例没有验证检索去重或效率最优。准入阈值行为来自 Fake 后端及现有 admission 逻辑，不代表 Milvus 的召回质量、向量一致性或真实文献可靠性。Research Worker 的一次结果走 `legacy_text` 解析，后续 Handoff 和最终覆盖仍完成；这说明当前兼容路径被实际使用，**不能据此证明所有真实模型输出均符合直接结构化 JSON 协议**。

## 离线故障注入证据（不是本轮真实模型实验）

| 故障 / 机制 | 现有离线证据 | 已证明的边界 |
| --- | --- | --- |
| Worker 结构化协议异常 | [`test_worker_protocol_recovery.py`](../tests/test_worker_protocol_recovery.py)、[`test_release_integration.py`](../tests/test_release_integration.py) | ScriptedLLM 注入无效协议；单次有界序列化恢复可成功，恢复无效时停止，不无限调用；正常 JSON 不触发恢复。真实 A1 主验收没有触发该恢复。 |
| 基础设施瞬时错误 / 重试 | [`test_runtime_reliability_closure.py`](../tests/test_runtime_reliability_closure.py)、[`test_release_integration.py`](../tests/test_release_integration.py) | 离线注入连接/超时等错误，验证有界重试、成功恢复、重试耗尽返回 incomplete，以及工具副作用不重复。另有前述旧网关真实运行发生一次重试，但不替代系统性故障注入。 |
| 上游失败、超时、空答案阻断下游 | [`test_multi_agent_handoff_audit.py`](../tests/test_multi_agent_handoff_audit.py)、[`test_release_integration.py`](../tests/test_release_integration.py) | 离线验证 `dependency_blocked`、下游 LLM 不被调用、最终不虚报 completed；真实依赖案例只覆盖上游成功路径。 |
| 历史预算、去重与 Session 隔离边界 | [`test_session_memory_budget_audit.py`](../tests/test_session_memory_budget_audit.py) | 离线覆盖预算 0/None、长历史裁剪、禁用、重复消息等；真实三轮仅覆盖预算内正常读取与跨 Session 隔离。 |

这些是既有测试的能力证据；本报告编写期间**没有重新运行测试或故障注入**，也没有将 ScriptedLLM/Fake 后端算作真实模型或真实医疗服务。

## 复现方式与审计材料

1. 在可信的本地环境配置 `MEDAGENT_LLM_BASE_URL`、`MEDAGENT_LLM_API_KEY`、`MEDAGENT_LLM_MODEL`；非空 endpoint **和** Key 均需存在，避免意外落入 `DeterministicLLM`。把 `MEDAGENT_RETRIEVAL_MODE=off` 用于非 RAG 场景；Fake RAG 必须显式注入固定的两条合成候选（0.92/0.45），不能把空的默认 Fake 后端当作同一实验。切勿把 `.env` 或密钥写入报告。
2. 采用 [README Quick Start](../README.md#quick-start) 的运行环境，但需用长生命周期 `NativeMedAgentEngine` 执行 Memory 三轮；每次 CLI `run` 会新建 Engine，不能证明跨轮记忆。实验调用前用同一模型客户端包装器计数并设置**总量和每轮硬上限**，出现超限即停止；本次各冻结场景只执行一次，不自动重跑。
3. 使用合成输入：单 Worker 为单一安全问询；并行场景为两个互不依赖的合成病例；依赖场景为先判断、再以其为前提给条件性建议；工具场景允许示例工具；Fake RAG 为同一合成问题加固定两条候选；Memory 为 Session A 首轮提供独有合成代号/事实、A 次轮只问回忆、Session B 用相同问题且不重复 A 事实。**不注入 Planner 计划、不强制工具调用**。模型行为可能变化，本报告是上述 run ID 的观测，不保证逐字可重现。
4. 将 Trace 写到仓库外的受控目录；仅在本机核对 `run_start`、`request_spec_built`、`plan_created`、`llm_request/llm_response`、`worker_draft`、`worker_handoff`、`tool_call/tool_result`、`retrieval_query/retrieval_result`、`memory_read/write`、`final_request_coverage`、`presentation_transform` 和 `run_end`。原始 Trace 可能含合成病例全文、Prompt 和模型输出，**不得直接提交或公开**。表中 run ID 用于本地定位，不是公开可下载的证据包。当前仓库没有可一键重跑这组付费案例的已跟踪脚本。

## 未验证边界与关闭条件

- 真实 Milvus、真实医学知识库、真实患者数据和临床正确性/安全性均未验证；示例临床工具不是临床服务。本项目不是医疗器械，不能用于实际诊疗决策。
- Coverage 是 RequestSpec ID 级结构覆盖；依赖场景 `RQ1` 粒度不足，且覆盖不等于语义正确。`legacy_text` 兼容路径、重复检索和真实模型输出漂移仍需单独观察。
- 未在真实 LLM 多轮中压满 Memory 预算或触发同 Session 去重；未用真实 LLM 触发协议恢复、重试耗尽或上游失败。离线测试对这些控制流有证据，但不能替代真实提供商故障分布。
- 如 V1 A1 的退出条件是“验证已冻结场景的真实模型接入与主要控制流，并如实记录局限”，**可以关闭 A1**。若退出条件包含“每个复合问询的独立条目覆盖、真实 Milvus 效果、真实故障注入或临床有效性”，则**尚不满足**；这些应作为后续独立验收项，不能修改本次 PASS/PARTIAL 结论。

分支核对：报告编写前本地 `integration/medagent-release` 为 `53105b4500bb6519deba2bdd483d66c247e2fc84`；只读查询的远程 `origin/release/github-ready` 也是该 SHA。此文档只是本地新增文件；未创建 Commit、未 Push，也未覆盖已发布历史。
