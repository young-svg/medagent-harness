# W7-2C 阻点修复：逐 RQ 冻结证据要求

> Historical implementation-stage record. Original counts and planned/pending labels below describe that stage, not the integrated candidate's present scope. Current code, frozen data and Runner are included here; see [release review](FINAL_RELEASE_REVIEW.md), [Runner guide](../eval/w7/README.md) and [W7 results](W7_REAL_MODEL_RESULTS.md). No historical experiment conclusion is rewritten.

基线：`01271ec5a9ab47d4ca8a02e815584251ff0c31e3`。
范围：已批准的可信 Python 冻结证据接口；不开发 Runner，不修改 frozen_v1、HTTP API、
VISIBLE_TOOLS、TOOL_SCHEMAS、历史实验或默认文本识别规则。

## 根因与接口

冻结 E03 的 RQ1 文本为“查询咳嗽编码”，原运行时文本识别没有得到 classification；
全局 Complexity 为 research，未开放 disease_code，RQ1 的 Evidence Gate 为 not_required。
不能改写冻结 RQ、Contract 或临时在 Runner 中提升权限来掩盖此差异。

`FrozenRequest.capture(..., external_evidence_requirements=rows)` 现在可接收冻结案例的
逐项声明。省略/None 保留旧路径和旧请求摘要；显式空列表代表没有外部证据要求，
不会再根据文本补出要求。已冻结案例文件不必、也没有被修改。

必要字段：request_item_id、required、source_category、acceptable_source_categories、
execution、upstream_request_item_ids。兼容冻结表中的 topic 和固定为 true 的
requires_admission_delivery_citation；其余字段拒绝，包括 success_source_ids、
expected_answer 和评分标准。没有把测试答案或来源示例白名单注入模型。

校验包括：RQ 存在且唯一、required 与 RQ 优先级一致、合法来源类型、非空且无重复的
允许类别、特定来源类型不能被其他类别替代、retrieve 无上游、reuse_only 有合法上游、
上游必须有兼容的证据声明、必答复用不能依赖可选来源，以及复用关系无环。
external 保留其允许类别范围，不强制所有来源标为 external。

声明保存为规范 JSON 字符串；类别/上游按集合语义排序、行按 RQ 顺序排列。
构造、capture、dataclasses.replace 使用同一校验/规范化入口，不保留传入 List/Dict。
读取返回不可变值对象的 tuple。存在声明时其全部规范内容纳入 FrozenRequest SHA。

## 唯一规范的传递路径

1. context/evidence_spec.py 定义经过校验的 EvidenceRequirement，集中来源和交付策略。
2. analyze_frozen 从 FrozenRequest 取同一 tuple；普通 analyze 不启用覆盖。
3. Complexity 的类别集合来自声明而不是文本，E03 因而成为 mixed。
4. EvidenceBundle 持有同一规范；工具注册表持有相同声明并与现有能力和角色白名单取交集。
   schemas_for 按任务过滤，execute 再按具体目标 RQ 过滤；不能用同 Worker 中另一 RQ
   的检索权限越过范围。reuse_only 不允许发起新检索，角色无权限仍拒绝 Handler 执行。
5. Planner 的 current_context 和 Worker 的结构化输入携带规范化要求，未加入来源 ID、
   rubric 或标准答案。Worker 输入只单独列出 assigned RQ 的声明。
6. Worker 和 FinalAcceptance 继续调用同一个 evaluate_evidence_coverage；它从 bundle
   读取声明，不再从该 RQ 文本推断类别。没有新增第二套完成判定或医学事实验证器。
7. evidence_transfers 使用同一声明验证显式复用上游、来源类别、工具及真实准入/引用链。
   传递记录保留 origin_request_item_id，并记录直接 upstream_request_item_id。
8. C 的冻结计划及重放校验请求 SHA 和执行条件 SHA；条件也包含实际工具声明。
   原有 plan SHA 仍表示完整 Plan JSON 的内容摘要，外层 request/conditions SHA 绑定
   新证据规范；不会把“任务图未变”误当“运行要求未变”。

Plan 校验要求 reuse_only 列明的上游 RQ 在实际任务依赖的祖先链中。
缺失关系在调度前拒绝，不自动补边、重规划或修改任务。当前要求可追踪的跨任务交接，
不支持把同一单任务内的隐式跨 RQ 共享冒充合法 Handoff。

Guard 延续模型/Memory/语料保护，并对注册表及 bundle 的实际证据声明增加漂移检查。
默认 metadata 隐私策略不变，未新增输出原始资料的白名单。

## 反例与边界

定向测试使用冻结案例原文和合成语料，实际执行 NativeEngine、Planner/Policy、
AgentLoop、ToolRegistry、Handoff、共享 FinalAcceptance 与 W2；只使用 ScriptedLLM。

- RQ1 未查或编码无结果：required=true，不会 not_required，不能完成。
- 两类合法来源分别检索并引用：每项 Evidence Gate 通过，可完成。
- RQ2 只收到编码来源：missing_evidence_kind；RQ1/RQ2 交换引用：invalid_citation。
- 同一 Worker 内错误目标 RQ 的 disease_code 被拒绝，Handler 不执行。
- 独立 Worker 保留逐 RQ 状态；同一 WorkerResponse 一项失败时沿用原有 Worker 原子失败，
  不把其中其他答案当作已成功交付，但保留 Worker 逐项诊断。
- consultation_agent 仍不能 disease_code；没有新增角色上限或工具 Schema。
- C/C-serial 计划及请求绑定一致；改变声明复用旧计划在模型、工具调用前拒绝。
- 可选指南缺失仍允许完成必答项；显式复用仅执行上游一次检索，下游越权新检索被程序拒绝。
- 非法映射、类别替代、优先级、复用循环/缺失/可选上游、标准答案字段和可变引用均有反例。
- 关闭冻结路径的词法证据函数仍能完成 E03，证明各消费者没有回退到正则。

合法引用和来源链不证明医学真实性、相关性、语义蕴含或证据充分性。声明为可信评测输入，
其人工完整性不能由程序证明。真实 Provider/Milvus 和统一 Runner 尚未验证。
HTTP400 仍 UNKNOWN、未修复。

本轮离线验证：新增 32 项测试；全部 W7 相关文件 120 passed in 4.60s；
完整离线回归 927 passed in 23.98s。Ruff 和 git diff --check 通过。
默认 analyze 与固定 W6 基线六类返回值/完整事件序列等价测试通过；
原有 W7 请求校验、模型/Memory/检索漂移及 C/C-serial 重放测试继续通过。
frozen_v1 的全部 20 个文件、24 例、40 个 RQ、29/3/8 静态检查及 SHA 均通过复验。
使用仓库外全新 pytest 临时目录，沿用仅对新临时树 mkdir 0700→0777 的 Windows
兼容方式，不改变历史目录权限。未 Commit、Push 或调用真实服务。
