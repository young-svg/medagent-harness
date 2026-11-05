# W7-2B 正式冻结 v1

版本：`w7-architecture-evaluation-v1`。状态：**APPROVED_FROZEN**。
批准依据：用户在本轮明确批准 candidate_v1 的 24 例及两项检索规则完善，并授权本地冻结提交。
这是工程评测数据批准，不是临床验证、医疗合规认证或真实模型性能验收。

## 内容和版本来源

- 上一级目录的 14 个文件逐字节复制自获批的 `experiments/w7/candidate_v1/`。
  cases.json、SCORING.md、RQ、Contract、依赖及评分标准均未修改。
- 原始候选文件内的 candidate/PENDING 标签及 fixture_version 原样保留，作为获批前
  快照的版本证据，不回写或伪造原审核记录。**当前冻结状态以本目录 manifest.json 为准。**
- 原始方案为 W7-1 报告第 4 节的 24 例，第 5～8 节提供夹具与实验协议。
  `../reference/` 保存两份用户提供的原始文件，摘要与获批 provenance.json 一致。
- 核心接口 Commit：`1f43fb003c99ec512c43e6cdf0196171bb542012`。
  原 W6 基线：`59a783f39e23bef514dffa7ab416373f11c2dca2`。
- 候选生成方式保存在上一级 prepare_candidates.py/schema_definition.py：对原表格
  显式转录与标注，不使用被评测模型的运行答案或业务正则作为标准答案。
  wording_changes.json 保存原文和候选表达差异，未在冻结时追加案例或评分修订。

## 校验

在仓库根目录执行只读校验：

```powershell
python experiments/w7/frozen_v1/freeze/validate_frozen.py
```

此命令先核验冻结目录的精确文件集合、每个文件 SHA-256、原始报告及获批快照，
再使用原样保留的校验代码执行全部数据合法性、原文映射、RQ/Contract、Session、
来源绑定、29 个 Query 样例、3 项 E05 非白名单检查和 8 项篡改反例。
原始报告从冻结 reference 目录读取，不依赖仓库根目录的未跟踪下载文件。
校验依赖同一仓库的 medagent Python 模块；不是独立的模型评测 Runner。

上一级 manifest.json/SHA256SUMS.txt 是原候选快照的清单，本目录 manifest.json
及 SHA256SUMS.txt 覆盖正式冻结包。manifest 不包含自身摘要，SHA256SUMS 额外记录
manifest 摘要；SHA256SUMS 不自哈希。最终 Git Commit 固定全部字节。

不要在 frozen_v1 内重新运行 prepare_candidates.py 或 --write-manifest。
变更须先形成新候选版本，人工批准后另发 frozen_v2，不覆盖 v1。

## 已知限制与后续输入

- 24 例为合成工程案例，不含真实患者资料，不代表临床有效性或泛化性能。
- 所有指南/论文/编码均为 SYNTHETIC；E01 current 只指固定夹具版本，非真实最新指南。
- 固定分数用于测试准入分支，不等同 Milvus 相似度、证据充分性或医学事实蕴含。
- Query 规则使用有限双语别名和概念合取；多主题上下文保守拒绝，不保证自然语言召回。
- 检索选择器与故障注入器未交付，29 个 Query 样例是静态规则校验，不是模型/检索实验。
- Memory Off 的安全澄清与任务完成分开计分，OPTIONAL 缺失不阻断必答完成。
- A/B/C/C-serial Runner、共同总预算、UNKNOWN usage 计量、盲审导入和真实实验仍待后续实施。
- 后续 C/C-serial 应共享同一冻结计划，执行层与 W1 端到端结果必须分开统计。
- HTTP400 继续 UNKNOWN、未修复；不更改历史 A1/A2/A3 或 60-case 结果。

本次冻结不授权真实模型调用、Milvus 服务、批量实验或 Push。
