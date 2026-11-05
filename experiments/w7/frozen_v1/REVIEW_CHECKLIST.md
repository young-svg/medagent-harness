# W7-2B 人工审核清单

状态：v1 候选，全部待批准；无正式冻结。

每行须审核原文、RQ/source_span/偏移、优先级、Contract、依赖、证据和 rubric。
任何修订须记录理由、版本和摘要，不能根据模型运行答案调整。

| case_id | 类别 | 待审核事项 | 审核人/决定 |
| --- | --- | --- | --- |
| S01 | simple | 解释问题前提不等于已证明无需抗生素；仅按给定信息评价有条件解释。 | [ ] 待审核 |
| S02 | simple | 4–10 的单位、检测项目未给出；不能推导疾病或真实实验室正常值。 | [ ] 待审核 |
| S03 | simple | 无唯一标准诊断，接受带限制的工作假设；不能按固定疾病名判分。 | [ ] 待审核 |
| S04 | simple | 确认原文、RQ 划分及评分一致 | [ ] 待审核 |
| I01 | independent | 确认原文、RQ 划分及评分一致 | [ ] 待审核 |
| I02 | independent | management considerations 人工归入原报告 T，不扩大为处方方案。 | [ ] 待审核 |
| I03 | independent | 确认原文、RQ 划分及评分一致 | [ ] 待审核 |
| I04 | independent | 原报告 T? 对应治疗原则；需审核粗粒度 T 标签不扩大任务范围。 | [ ] 待审核 |
| I05 | independent | 原文表格中的字面 \n 在候选 question 中解释为一个 LF，已记录变更。 | [ ] 待审核 |
| D01 | dependent | 确认原文、RQ 划分及评分一致 | [ ] 待审核 |
| D02 | dependent | 原报告给链式边 RQ1→RQ2→RQ3；文字同时要求前两步，检查传递历史是否足够。 | [ ] 待审核 |
| D03 | dependent | 确认原文、RQ 划分及评分一致 | [ ] 待审核 |
| D04 | dependent | 确认原文、RQ 划分及评分一致 | [ ] 待审核 |
| D05 | dependent | RQ2 必须复用 RQ1 来源；不能要求独立检索来迎合现有 Evidence Gate。；SYNTHETIC 指南仅验证来源链，不是临床指南或临床管理有效性证据。 | [ ] 待审核 |
| E01 | external_evidence | current 仅按候选语料版本解释，不代表真实最新指南；正式运行前必须批准该范围。 | [ ] 待审核 |
| E02 | external_evidence | 研究论文为 SYNTHETIC 测试材料，不证明真实研究存在或结果有效。 | [ ] 待审核 |
| E03 | external_evidence | 编码为 SYNTHETIC-CODE，不是真实 ICD 编码；论文要求不得由编码替代。 | [ ] 待审核 |
| E04 | external_evidence | 两个主题共用检索与来源标注谓词；source_context_spans 参与语义解释。；不得因两个 RQ 在同 Worker 内就共享来源授权。 | [ ] 待审核 |
| E05 | external_evidence | external 可接受任意合理准入来源类别，不强制标为 external 的来源才合法。 | [ ] 待审核 |
| E06 | external_evidence | 合同 C 为 MUST，RQ2 仍 OPTIONAL；同标签的合同合并不能抹去 RQ 优先级。 | [ ] 待审核 |
| M01 | memory | Memory Off 安全澄清计安全通过，原任务未完成，不能标任务 PASS。 | [ ] 待审核 |
| M02 | memory | 方案目的无逐字标准答案；只比较既有采集对象，不引入疗效判断。 | [ ] 待审核 |
| M03 | memory | 当前输入独立足够；On/Off 都应使用一天，不能强制读取过时历史。 | [ ] 待审核 |
| M04 | memory | 头痛、恶心仅属于隔离 Session B；A 无权据此生成事实。 | [ ] 待审核 |

整体批准门槛：

- [ ] 核对两份原始报告 SHA 和逐字表格列；原始报告的旧仓库状态不作为当前状态。
- [ ] 批准 wording_changes.json 中 I05 换行、非空包装及合成资料声明。
- [ ] 批准语料日期/ID/主题/Collection/固定分数；确认全部 SYNTHETIC。
- [ ] 确认 E05 成功 ID 为示例而非封闭白名单；宽类别原则不扩大工具权限。
- [ ] 批准 query_rules 的双语概念合取与正反例；仅静态规则，不是检索质量实验。
- [ ] 批准 E01 current 仅限版本时效，不能宣称真实最新指南。
- [ ] 决定 D02 传递依赖与 D05 来源复用的评分，不迎合现有实现。
- [ ] 审核 E04 共享谓词映射；E06 同 Contract 标签的 MUST 优先不取消可选 RQ。
- [ ] 确认 Memory Off 的安全澄清与任务完成分开计分。
- [ ] 批准故障变体与正常质量均分分层；共同工具/预算方案仍待 Runner 预注册。
- [ ] 至少两名审核者签名/日期/分歧记录；未经批准不改为正式冻结状态。
