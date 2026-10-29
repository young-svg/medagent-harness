# 已知问题：工具续接请求 HTTP400（根因 UNKNOWN，未修复）

归档日期：2026-10-29。范围：MedAgent V1，A3运行代码基线`fdf52d7`及其后新增的HTTP错误诊断。**真实模型诊断已结束，不再追加请求。** 本记录归档诊断能力，不宣称修复了400。

## 复现条件与观测

A3的R01/R03使用第三方OpenAI-compatible网关及模型别名`deepseek-flash`。首轮Worker自主调用检索工具，真实合成Milvus查询和证据准入完成；随后携带assistant.tool_calls及配对tool结果的Worker续接请求返回HTTP400。两题没有最终答案，系统返回incomplete并阻断依赖Worker。

在A3封存后，仅独立重放R03历史失败续接请求一次：messages、tools、模型、temperature=0及max_tokens=8192保持一致，未发送response_format；禁用重试、重定向及后续调用，没有运行完整Agent或重跑评测。

| 独立重放观测 | 结果 |
| --- | --- |
| 实际模型HTTP POST | 1次，后续0次 |
| HTTP状态 | 400 |
| 已保留的提供商type/code | invalid_request_error / invalid_request_error |
| 安全消息分类 | unclassified |
| 白名单请求ID / 参数位置 | 未取得可用值 |
| usage / 实际费用 | UNKNOWN，未返回usage，不能记为0或认定免费 |
| 耗时 | 2.129秒 |

该重放确认当前配置下相同可观察请求仍被拒绝，但不能证明它与历史两题是同一具体根因。独立重放不计入A3的20题、71次请求或100,206个已报告Token。原始Trace、重放脚本和结果只保存在仓库外，没有复制到本提交。

## 已核对的简单原因与证据边界

- R01/R03工具调用ID唯一，tool_call_id逐一配对，工具名称匹配，参数可解析且query为字符串；未发现重复ID、缺失配对结果或该参数的JSON错误。
- 两个失败请求均未发送response_format，不能解释为误加了该字段。
- R01的assistant.content为空，R03非空，因此空content不能单独解释两次失败。
- R04在同次A3运行中携带多条工具结果成功；R03/R04工具Schema一致，不能仅凭多工具或该Schema的存在确认原因。
- 三者均未保留reasoning_content，但现有日志不足以判定不同上游线路的实际协议要求；不能据字段缺失断言根因。

以上只排除了若干简单解释或未发现对应错误，不构成整个请求已被网关证明合法。历史Trace没有网关request ID或详细错误正文；独立重放仅得到通用invalid_request_error。

**当前历史故障和独立重放的具体根因均为UNKNOWN。** 不推断为Milvus、阈值、reasoning字段、空content、参数长度或网关转换错误。

## 已实现的最小脱敏诊断

- `medagent/llm/http_errors.py`：保留HTTP状态码、异常类型、白名单提供商类型/码、合法参数路径及固定消息分类；最大解析正文64KiB，消息分类只检查前4096字符。
- `medagent/llm/client.py`：继续抛出原类型HTTPStatusError并保留response供原有分类使用；异常字符串不再包含请求URL或不可信正文。
- `medagent/observability/llm.py`：向原有llm_response.error添加安全HTTP诊断；保留请求→响应父子关系。
- `medagent/llm/generation.py`：向原有worker_provider_attempt添加同一诊断；400仍不重试，429/5xx原有瞬时错误分类不变。
- `tests/test_http_error_diagnostics.py`：离线Mock覆盖诊断、敏感正文/Key/URL不进入新增错误日志、非标准/超长正文、成功协议兼容、重试分类和工具续接失败传播。

未知标签、任意自由文本、HTML和原始响应正文不写入诊断；这是一种保守的元数据保留策略，不是通用患者文本脱敏器。原始httpx.request/response仍因兼容性留在异常对象上，调用方不得整体dump；既有原始审计Trace的请求内容保留策略未被修改。

诊断仍有限制：没有生产侧响应request ID采集；未识别错误会保持unclassified。提供商消息分类仅表示其措辞，不等于独立确认原因。当前实现未能确定此次真实400根因。

## RAG交付影响与后续范围

检索后端查询及证据准入成功不保证最终回答交付。R01/R03续接失败导致两题缺答；依赖任务被正确阻断、未调用下游LLM、没有虚报completed。这一失败必须保留，不能以检索成功、工程测试通过或脱敏诊断上线宣称端到端已恢复。

后续最小方向：优先在现有授权范围内完善安全诊断或读取既存网关日志，取得明确错误位置后再修具体协议。账户日志通常需要独立AccessToken，消费日志也不一定包含未扣费失败；当前并未取得可关联的账户错误记录。不得盲目重试400、修改Prompt、路由或RAG协议。

A3原始结果、人工审核和正式报告已封存，本归档不改变其成绩。本次提交仅包含5份诊断代码/测试和本记录；A3文档另行归档。A4开始前应明确其目标与验收边界，并将本问题列为尚未修复的RAG交付风险，不能把A3关闭等同于该故障修复。
