# W7-2：可信评测接口与共享最终验收

> Historical implementation-stage record. Original counts and planned/pending labels below describe that stage, not the integrated candidate's present scope. Current code, frozen data and Runner are included here; see [release review](FINAL_RELEASE_REVIEW.md), [Runner guide](../eval/w7/README.md) and [W7 results](W7_REAL_MODEL_RESULTS.md). No historical experiment conclusion is rewritten.

基线：W6 Commit `59a783f39e23bef514dffa7ab416373f11c2dca2`。

状态：W7 核心接口离线工程验收通过，冻结提交；尚未完成真实模型或成本验证。

本轮范围：三项最小核心扩展、FIX-1～FIX-4，以及检索后端/Fake 语料运行期漂移修复（共五项针对性修复）。未实施完整 W7 评测体系、24 例数据集、总预算适配器或真实模型实验；不启动 W7-3，不修改历史实验和 HTTP400 UNKNOWN 结论。

## 1. 可信冻结请求入口

`runtime/evaluation_inputs.py::FrozenRequest.capture` 将人工 RequestSpec、AnswerContract、restrictions 和病例/问题保存为不可变 JSON/tuple 快照，读取时产生新的业务对象。后续修改原对象不会改变快照。

校验包括连续 RQ ID、顺序、布尔类型、来源片段、既有交付类型、规格/合同范围与 MUST/OPTIONAL 一致性，以及禁止事项进入 avoid。保留既有默认安全 avoid。restrictions 必须对应当前问题中的原文片段。

人工语义映射以 RequestItem.semantic_type 为依据：合法交付类型精确映射到同名交付；UNKNOWN/None 仅映射到 COMPREHENSIVE_CASE_ANALYSIS。各 RQ 的映射取并集构成合同范围，必答 RQ 的映射构成 MUST，其余为 OPTIONAL；同类型同时出现在必答和可选 RQ 时，合同层 MUST 优先，RQ 本身的优先级不变。多交付要求应在人工规格中明确拆项，不能用正则从单项自动扩出额外交付。

不再使用 contract_for_spec 的文本正则推断否决人工合同；例如 Is surgery indicated? 声明 SURGERY_INDICATION 时可合法执行。禁止范围只对人工标注的完整规范禁止子句做封闭名称映射，不按关键词出现来判定；含引用、条件、例外或多个子句的开放文本由人工审核和 W2 检查。合同不得同时要求执行已确定的禁止范围。测试保留“禁止诊断、仅讨论手术指征”这一混合子句的合法 SURGERY_INDICATION，避免再度误拒绝。

`NativeMedAgentEngine.analyze_frozen(request, session_id=None, *, plan=None)` 是可信 Python 内部评测入口，返回 `EvaluationRun(result, plan)`。它跳过两次 W1 语义提取，要求语义验证开启并在 Planner 前保留至少一次 W2 调用预算。保留已有 Memory 快照、结构 Coverage、Guardrail、安全编辑、Evidence Gate、W2、失败关闭和最终状态判定。

人工规格的语义完整性由案例审核负责；程序校验不能证明人工没有漏写要求。该入口不是不可信用户上传规格的授权机制。

默认 `analyze(description, question, session_id=None)` 签名和返回结构不变，仍走原有 W1。HTTP API 没有新增参数或端点；FrozenRequest 不通过 API schemas 接收。

## 2. 最终计划导出与 C-serial 重放

C 的可信入口仍调用既有 Planner 和 Policy；在它们完成后生成 `FrozenExecutionPlan`。仅在验证通过后进入调度。计划快照通过 `to_dict/from_dict` 序列化，不依赖 raw Trace 导出。

计划 SHA-256 对 `Plan.to_dict()` 的 UTF-8 规范 JSON 计算：键排序、无格式化空白、保留数组顺序、禁止 NaN。摘要包含任务、角色、RQ/交付映射、依赖、说明及既有 Planner/Policy 元数据。它不是签名或来源认证。

C-serial 要求配置 `max_concurrent_workers=1`，传入同一个计划快照。它不调用 Planner，不再次调用 Policy，不更换角色、重新生成任务或截断任务列表。读取快照后验证内容摘要、DAG、任务数、RQ/交付映射和必答范围。

快照还绑定冻结请求摘要和执行条件摘要。条件包括运行配置、实际 Memory 实现类型及 enabled/recent_limit/token_budget、实际注入历史、角色规范版本、各角色实际暴露的工具 Schema、客户端声明的模型、展示转换启用情况，以及后端类型；Fake 后端还绑定预置资料。Memory 配置取自注入实例，不能以 RuntimeConfig 代替。只有 Trace 目录与 Worker 并发度从跨运行配置比较中排除。改变预算、工具权限、Memory 配置或历史等条件将拒绝重放，而不是尝试修复计划。

工具权限校验仍由现有 ToolRegistry 执行。快照验证角色及其可用工具集合没有变化，不根据计划或模型声明提升权限；计划本身不携带可绕过权限的工具执行命令。

示意调用（复用已构造且条件一致的两个引擎）：

```python
request = FrozenRequest.capture(description, question, spec, contract, restrictions)
c_run = await c_engine.analyze_frozen(request, session_id="evaluation")
assert c_run.plan is not None
saved = c_run.plan.to_dict()
serial_run = await serial_engine.analyze_frozen(
    request, session_id="evaluation", plan=FrozenExecutionPlan.from_dict(saved),
)
assert serial_run.plan.sha256 == c_run.plan.sha256
```

两个引擎应使用相同初始历史、模型及工具/检索条件；不能让串行组继承 C 运行后新写入的会话历史。模型客户端和 mutable Fake fixture 应分别实例化或重置到相同初始状态。

## 3. Trace 与拒绝行为

复用现有 `request_spec_validation`、`plan_created` 和 LLM 事件，不新建事件系统。可信评测路径记录：

- `request_source=frozen`、`request_sha256`：明确 W1 被跳过。
- `plan_source=planner_policy/frozen_replay`、`plan_sha256`、`execution_conditions_sha256`。
- `planner_call_count`：本次运行中实际 `stage=planning` 的 llm_request 数，包括长度恢复；重放为零。快照内的 Planner 元数据是历史来源信息，不是重放调用量。

metadata 仅新增固定来源枚举、非负整数和严格小写 64 位十六进制摘要；不恢复计划正文、病例、历史、Prompt 或异常正文。既有 UUID 因果关系不变。

非法冻结输入在内部入口拒绝，可能尚未建立 Trace。计划校验失败发生在调度前，记录 failed Trace 并抛出异常，零 Worker 调用；任务数超限沿用规划预算异常分类。W2 关闭或预算不足不能继续生成答案。评测调用方必须将这些异常记录为失败，不能排除出统计分母。

## 4. 共享验收服务

`runtime/acceptance.py::FinalAcceptance.evaluate` 是从 Native 原实现移出的唯一验收服务，负责：

1. Worker 结构 Coverage、依赖阻断和初始 Evidence 检查；失败时禁止合成，产生原有缺失项及失败 Trace。
2. 在允许时调用传入的合成函数；Native 保持至少两个成功 Worker 才合成的条件。
3. 原有清洗、有限安全编辑、Worker 回退与阻断累积。
4. 最终 RequestSpec/Contract Coverage、Evidence Gate、Guardrail 硬阻断及 W2。
5. 原有 completed/incomplete 和缺失项判断。

`finalize_answers` 只保留一份实现。Native 的 `_finalize_answers` 是委托兼容层，保留已有 Python 调用及 sanitizer 注入测试。默认 Native 的 Memory 写入、展示转换、生命周期结束和返回组装仍留在引擎，顺序不变。

A/B 可将真实 AgentLoop 执行结果传入该服务。A 无 Planner，B 可先 Planner 再由同一个 Worker 顺序执行；调用方提供经过校验的 Plan、WorkerResult、EvidenceBundle 和同一 W2 验证器。A/B 不必调用多 Worker 合成，服务从逐项 Worker 答案进入同一验收。服务本身不替代调度器，也不执行任意模型生成的计划命令。

测试中的 A/B 小型执行 harness 仅证明接口可复用、路径不同和门控一致；它们不是已经交付的完整公平对照适配器。

## 5. 行为等价证据与离线验证

测试从固定 W6 Git blob 读取原 NativeEngine，在独立命名空间执行，分别与当前默认 analyze 运行同一输入和 ScriptedLLM。比较完整返回值及事件序列，仅规范化 UUID、timestamp 和 latency_ms；保留事件关联和业务字段。覆盖 completed、结构 incomplete、硬阻断、证据失败、W2 跑题及可选项缺失。该测试需要本地保留基线 Git 对象，不联网、不写历史 checkout。

新增测试还覆盖：快照不受原对象修改影响、无 W1 但实际 W2 调用、禁止关闭/耗尽 W2、非法来源/合同优先级、摘要篡改、非法 DAG/角色/RQ/交付映射、任务数/执行预算/权限变化、上游失败阻断下游、序列化往返、重放不调用 Planner 或 Policy，以及 metadata 的摘要字段防正文透传。

A/B/C 的小型真实执行路径使用同一验收服务，覆盖成功、部分回答、硬阻断、证据失败、W2 失败、可选项和禁止事项。两步案例实际调用数分别为 A=1、B=3、C=4（不含共同 W2）；C 保留合成。独立双 Worker 案例观测 C 的实际峰值并发为 2、C-serial 为 1，计划及条件摘要相同。

2026-11-04 验证结果（全部为 ScriptedLLM / Mock / 合成资料，无真实 Provider）：

- 验收代码抽取后、增加新接口前：原有完整回归 807 passed in 18.82s。
- 两个 W7 新测试文件：43 passed in 2.39s。
- 三项接口初轮完整离线回归：850 passed in 21.71s，其中保留原有 W1～W6 的 807 项。
- `ruff check --no-cache medagent api tests`：通过。
- `git diff --check`：通过；新增文件另做尾部空白检查。
- `api/`、Planner、AgentLoop、ToolRegistry、历史 A3 和 HTTP400 文档无改动。

pytest 使用仓库外全新 `--basetemp`，禁用 pytest 缓存写入。为兼容既有 Windows 临时目录权限问题，仅在测试进程内将该新临时目录树的 mkdir 0700 改为 0777；不改历史目录权限或仓库测试配置。最终无测试失败或跳过。

## 6. 四项审查修复与运行级漂移保护

- FIX-1：采用上述确定性人工语义映射，拒绝非法类型、优先级冲突、禁止范围冲突，以及计划中跨 RQ 串换交付映射；不再以正则结果否决人工 Contract。
- FIX-2：绑定实际 Memory 配置、实现类型和历史；即使历史均为空，Memory Off/On 也产生不同条件摘要并拒绝不一致重放。
- FIX-3：仅在 analyze_frozen 中启用 evaluation_guard.py。运行开始捕获本地模型、配置和 Memory 的有效状态，比较基准保存为不可变规范 JSON。Planner 返回后、Worker 获得槽位时、每次模型调用前后、最终验收及结束前重新读取当前状态；覆盖恢复、Synthesis、W2 和展示转换。工具 Schema 也加入运行内检查。漂移锁存，原值恢复也不能解除；最终抛出 EvaluationConditionDriftError，run_end=failed，不返回有效 EvaluationRun。
- FIX-4：FrozenRequest 和 FrozenExecutionPlan 的直接构造、capture、replace 统一通过 __post_init__ 规范化 JSON 和标量。restrictions 的 List 转成仅含字符串的 tuple，不保留外部引用；JSON 拒绝重复键、NaN 和非对象根。materialize 每次返回新对象并校验业务映射/摘要。修改解冻后的 Plan、依赖或导出的 dict 不会修改快照；若重新序列化却不更新摘要，将拒绝使用。

评测模型包装器显式传递运行捕获的 temperature 和各阶段 max_tokens 上限，保留 W2/协议修复的显式覆盖。标准 OpenAI-compatible 客户端支持这两个参数；MockTransport 测试验证实际 HTTP 请求体使用捕获值，且 Planner 期间漂移后不再调用 Worker。任何自定义客户端必须实现 LLMClient 的关键字参数协议；包装器不能保证任意客户端会遵守参数，也不控制 Provider 私有内部状态、隐藏重试或服务端版本。

安全 Trace 只新增固定原因枚举 model_parameters_drift、runtime_configuration_drift、memory_configuration_drift、tool_permissions_drift 和 evaluation 阶段；不记录观察到的参数值、密钥或敏感配置。llm_request 仍表示调用尝试：边界拒绝可能发生在该事件记录之后、实际客户端调用之前，不能仅凭它计算网络请求或账单成本。漂移不得因伴随网络异常而被误分类为可重试基础设施故障。

2026-11-04 四项修复最终验证：

- 新增反例文件 test_w7_frozen_interface_fixes.py：36 项通过。
- 新增反例及既有两个 W7 文件合计：79 passed in 3.07s。
- 完整离线回归：886 passed in 23.82s；原有 850 项全部保留、通过，无失败或跳过。
- Ruff、git diff --check 通过；默认 W6 六类返回值/完整事件序列等价测试，以及 C/C-serial SHA、零 Planner 重放和实际并发测试继续通过。
- 额外离线探针确认参数不变时保留有限基础设施恢复：一次合成 ConnectionError 后 completed，业务模型调用 3 次（Planner + 两次 Worker 尝试），恢复计数 1。

## 7. 尚未解决的公平性与适用边界

### 检索运行条件漂移补丁（独立审查 P1）

此前 conditions SHA 已包含 Fake 语料，但 Guard 未持续检查后端及语料，
Planner 等待期间的变化可能使 Worker 使用与记录摘要不一致的证据。
仅在 analyze_frozen 中新增 retrieval_conditions_drift 检查：绑定实际后端实例、
检索模式、实现类型，以及全部 Fake EvidenceItem.to_dict()（含嵌套 metadata）。
复用 Guard 的规范 JSON 快照和锁存失败机制；Planner 返回、各模型调用边界，
以及实际 backend.search 前后均检查，变化时终止评测，不返回有效 EvaluationRun。
检索异常路径同样重新检查，避免将语料漂移误当成普通工具错误继续执行。
Trace metadata 仅增加固定安全原因码，不记录语料、查询或变化后的配置正文。

_evaluation_retrieval 同时供 conditions SHA 和运行内检查使用，避免两套语料定义。
实例身份只用于同一次运行内检查，不加入跨运行摘要；内容相同的独立 Fake 实例
仍可用于 C/C-serial，内容不同则重放失败。_tool_registry 新增仅内部使用的
可选 evaluation_guard 参数；普通 analyze 不传此参数，检索行为保持不变。

新增 tests/test_w7_retrieval_drift.py 的 9 项测试通过真实 NativeEngine 覆盖：
Planner 期间修改列表、EvidenceItem 字段、嵌套 metadata、替换后端，
原有模型/Memory 漂移、实际检索前/检索中的修改，以及相同/不同语料重放。
断言实际 backend.search 次数、模型续接和 run_end 状态；不能只以摘要差异证明阻断。

边界：这不是远端 Milvus/Provider 状态冻结，也不是任意自定义后端的隔离沙箱。
不能撤销已发出的请求，不能保证发现两次检查之间修改后立即恢复的内容，
也不提供线程级原子事务。Fake 快照检查增加随语料大小增长的本地 CPU/序列化开销，
不增加模型调用次数。失败运行应丢弃后重新准备独立夹具，不自动重试整次实验。

本补丁离线验证：新增反例 9 项通过；全部 W7 相关测试 88 passed in 3.49s；
完整离线回归 895 passed in 22.17s；Ruff 与 git diff --check 通过。
包含原有 W6 默认执行路径等价、模型/Memory 漂移、C/C-serial 重放及安全门控回归。
首次 pytest 遇到 Windows 新临时目录 0700 权限问题；后续仅对全新仓库外临时树
采用 0777 创建模式运行，不修改历史目录权限或项目测试配置。

- 尚未提供完整 A/B/C/C-serial 评测 runner、人工冻结的 24 例、统一运行总预算和 UNKNOWN usage 统计；本轮不宣称 W7-2 全体系交付完成。
- 现有 W4 是每子任务预算；本轮不将它冒充整次运行成本上限。A/B 提示词、工具共同子集、合成开销和恢复额度仍需在正式适配器中预注册。
- 条件摘要验证可观测/声明的条件，不能冻结外部 Provider 实际版本或远端数据库内容；Milvus 语料/索引/嵌入版本仍需独立冻结清单。没有开展真实模型或 Milvus 质量实验。
- 参数保护针对受信任客户端的公开有效配置与标准调用覆盖，不是任意 Python 客户端的隔离沙箱；不能撤销已经发出的请求或已发生的工具副作用。失败评测应丢弃结果，并重置客户端、工具夹具和 Session 后再试，不自动重试整次运行。展示阶段失败前可能已有正常 Memory 写入，当前没有跨阶段事务回滚。
- W2 使用同一程序约束和失败关闭，不等于拥有独立医学标准答案；摘要和合法引用也不证明事实正确或语义蕴含。
- 固定规格实验与默认 W1 端到端实验必须分开。不得将跳过 W1 的调用量、成功率混入默认产品路径统计。
