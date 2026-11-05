# W7-2B v1 候选案例与夹具

**CANDIDATE_PENDING_HUMAN_APPROVAL：尚未批准，尚未正式冻结。**

核心接口已提交：`1f43fb003c99ec512c43e6cdf0196171bb542012`。
这里的文件独立于运行时，不修改 W1～W6，不提供 A/B/C Runner，不调用模型或数据库。

## 来源与文件

依据仓库根目录用户提供的 `W7-1_original_design_report.md` 第 4 节，
并与 `W7-1_24_case_draft_original.md` 的 24 行逐字段对照。
第 5～8 节提供夹具、故障、评分和公平性依据。来源摘要在 `provenance.json`。
原报告的旧分支/Commit 状态为历史记录，不作为当前仓库状态。

| 文件 | 内容 |
| --- | --- |
| cases.json | 24 例、40 个 RQ、原文、背景、会话布局、人工预期转录、依赖及逐例评分 |
| wording_changes.json | 全部实际输入调整的 before/after 与理由，不覆盖原始文本 |
| retrieval_corpus.json | 9 条 SYNTHETIC 来源、RQ 绑定、36 个响应变体、Query 规则和 29 个正反例 |
| fault_variants.json | 18 个独立故障变体定义；未注入、未执行，不增加基础案例数 |
| schema.json | JSON Schema 2020-12，限定候选文件字段、类型和状态 |
| schema_definition.py | 可复现生成 Schema；没有新的框架依赖 |
| prepare_candidates.py | 明确逐项标注后转录数据，拒绝原始表格不一致；不根据业务正则拆项 |
| validate_candidates.py | 标准库静态检查、只读业务 Schema 校验和 8 项篡改反例 |
| REVIEW_CHECKLIST.md | 每例待审问题与整体批准门槛；全部未勾选 |
| SCORING.md | 分类评分、信息不足、故障、成本及人工盲审口径 |
| manifest.json / SHA256SUMS.txt | 文件版本和精确字节 SHA-256，不代表批准或签名 |

原始两份文件不移动、不修改、不自动纳入 Git。对外传递候选数据时也需提供原文，
否则校验器无法确认来源一致性；只复制候选目录不能代替原文审核。

## 运行静态检查

从仓库根目录：

```powershell
python experiments/w7/candidate_v1/validate_candidates.py
ruff check --no-cache experiments/w7/candidate_v1
```

需要有意重建候选文件时才执行：

```powershell
python experiments/w7/candidate_v1/prepare_candidates.py
python experiments/w7/candidate_v1/validate_candidates.py --write-manifest
```

重建及更新摘要不授予人工批准。校验器不是通用 JSON Schema 引擎；它只实现生成
Schema 内使用的验证关键字，遇到未知验证关键字拒绝处理。再增加规则须扩展检查器。
它也调用 `FrozenRequest.capture` 验证业务协议兼容性，但不调用 analyze、工具或模型。
协议兼容只是一项附加检查，不能反向改变原始人工预期以迎合运行时。

## 原文和候选输入的差异

- 每例保留 `original_design.table_columns` 和原始输入，原 case_id 不变。
- 仅 I05 的问题内容作表达层转换：字面 `\n` 变为一个 LF，以表达原方案的多行问题。
  原始问题仍保留；同时提供原始与候选 question 的独立字符区间。
- E04、M01～M04 没有独立病例描述，添加非临床的 SYNTHETIC 非空包装。
  这不是补充病史，须人工批准。
- D05、E01～E06 的 description 添加合成资料声明，原问题不重写。
  E01 的 `current` 仍保留，只能在批准的夹具版本范围解释；不能宣称真实最新指南。
- 原文中的“指南”“论文”“疾病编码”在测试中分别使用 SYNTHETIC 类别记录；
  无真实 DOI、期刊、机构指南或 ICD 代码，也不宣称真实医学结论或时效。
- 这些调整、语料正文、固定日期/标识、评分细化及 Schema 均是助手补充的候选标注，
  不代表专家已审定，也不使用被评测模型运行输出作为标准答案。

## source_span 与优先级

偏移采用 **Python/Unicode 码点、从零开始、左闭右开 `[start,end)`**。
`source_mappings` 的主区间相对于候选 `question`；`original_question_span` 相对于
保留的原始问题。不是 UTF-8 字节或 JavaScript UTF-16 索引；LF 计一个码点。

E04 两个主题共享“检索论文并标注来源”的谓词，各自主题作为主 span，
共享谓词列为 `source_context_spans`，不得仅凭主题词丢掉完整任务语义。
I02 与 D03 的共享上下文也显式保留。RQ ID 按正向要求原文顺序，禁止事项进入
restrictions/avoid，不伪造为必须执行的 RQ。

Contract 类型沿用原报告 C/D/T/F/P/DIFFERENTIAL_DIAGNOSIS，未调用文本正则推断。
I04 仍为 C MUST、T OPTIONAL；E06 两项都为 C，所以合同层只有 C MUST，
RQ2 仍为 OPTIONAL。合同标签合并不能取消 RQ 级优先级，也不能扩充治疗范围。

## 检索、故障与后续 Runner 边界

阈值 0.63、top_k=5、成功分数 0.90、低分 0.20 来自原方案；来源标识、
日期 `2026-11-04` 和全部短正文为候选夹具细节，需人工批准。
Collection 使用现有 `clinical_knowledge` / `clinical_guidelines`，后续需固定配置摘要。
`search_knowledge` 可取一般资料/指南/论文，`clinical_guideline` 为特殊 Collection，
`disease_code` 用于编码；不要求所有架构拥有当前角色以外的额外权限。

检索变体提供具体内容和分数，但必须先按实际工具、Collection、主题筛选，
不能直接把记录全量传入 Native Fake 并宣称实现了检索选择。
未来选择器应同时绑定 case/RQ 与有效 query/collection；不得仅按 RQ 返回答案，
忽略错误查询。E05 接受所有合理来源类别，所以 wrong_category 标记不适用。

### 冻结前完善：来源示例与 Query 规则

本轮只修改检索选择定义、Schema、静态验证及相关文档；24 例的 cases.json
（包括输入、RQ、Contract、依赖和 Rubric）逐字节不变，仍待最终人工确认。

所有绑定的 `success_source_ids_semantics=catalogue_examples_not_whitelist`：
成功 ID 是当前目录中的示例，不是授权白名单，也不能凭一个 ID 自动通过。
候选来源须真实存在于目录，并满足本 RQ 的主题、类别、实际工具、Collection；
后续执行仍须证明检索、准入、送达与引用，不因示例 ID 存在就绕过 Evidence Gate。

E05 的 acceptable_source_categories 保留 guideline/research/classification/external，
不要求来源必须标为 external。当前既有 search_knowledge + clinical_knowledge 可取
SYNTHETIC-G-COUGH、SYNTHETIC-R-COUGH、SYNTHETIC-X-COUGH，三个类别均有正例。
现有编码记录位于 clinical_guidelines，因此当前 E05 不可据此扩大工具或 Collection；
类别层允许 classification 不代表当前编码记录可被该工具取到。
校验器另将 E05 示例列表临时缩减为仅 external，确认目录内指南和论文仍可按规则入选。

`query_rules.version=w7-query-selection-v1.1-candidate` 定义：

- 先做 Unicode NFKC、casefold 和空白折叠。
- 主题内全部概念组须满足，每组允许多个别名。例如运动/锻炼/physical activity/workout
  与睡眠/入睡/sleep/nighttime rest 都须出现；不是任一单词即可通过。
- 饮水/补水/fluid intake 与头痛/头疼/head pain 同样采用概念合取。
- 英文别名按词或词组边界匹配，中文按子串；sleep 不能匹配 oversleep。
- Query 独立识别主题后还须与 RQ 范围一致；空查询、错误主题及混合主题拒绝，
  不允许按 RQ ID 兜底返回正确来源。错误 Collection、未授权工具也拒绝。
- 示例 ID 不是筛选条件；来源目录、主题、类别和工具/Collection 才是筛选条件。

29 个手写预期样例覆盖 E02、E04 的中英文同义表达及错误主题/Collection，
并包括 E05 三类来源、无新工具授权、空查询、缺失概念、子词误命中、歧义主题等。
预期结论不是由匹配器自动生成。`select_fixture_candidates` 只是验证脚本中的纯数据
参考谓词，不调用工具，不作为后端注册；selector_contract.implemented 仍为 false。

规则只覆盖预注册的有限别名，不保证任意自然语言改写、否定语义或检索召回率。
若完整 QueryBuilder 上下文混入其他完整主题，当前保守拒绝歧义；未来适配器须如实报告
误拒绝，不能偷偷忽略 Query 或按 RQ 返回答案。此处没有开发或验证完整检索适配器。

D05 RQ2 只消费 RQ1 显式 Handoff 的来源，不允许另检索；该项真实运行兼容性尚未验证。
若当前门控无法支持，报告实现限制，不能改标准绕过。依赖边是逻辑要求，
不强制任务名字或每 RQ 一个 Worker；同 Worker 内的顺序与真实使用也需审计。

故障文件是声明式设计，不是已完成的故障注入器。共同工具、每例共享总预算、
协议恢复关闭策略、完整计量以及 A/B/C/C-serial 入口仍待下一阶段实现和审核。
HTTP400 继续 **UNKNOWN、未修复**，不修改历史结论。

## 批准后的下一阶段输入

需人工批准的案例/语料/故障版本和 SHA、评分分歧记录、共同工具与预算方案、
SYNTHETIC 时效范围、Memory On/Off Session 种子布局、模型与配置版本清单。
执行层与 W1 端到端实验须分开，C/C-serial 共享完全相同的导出计划。
这些候选文件本身不构成运行授权，也不构成模型质量实验结果。
