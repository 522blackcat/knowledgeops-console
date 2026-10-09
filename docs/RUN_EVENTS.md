# Agent Run 事件流规范

这份文档定义聊天窗口中一次 Agent 运行的展示顺序和事件含义。它对应产品体验，不只是后端日志。

## 前端展示顺序

用户点击发送后，聊天窗口必须按这个顺序展示：

1. 已存在的历史对话。
2. 本次用户问题，立即展示，不等待后端返回。
3. 本次运行过程。
4. Agent 最终回答。

运行过程只在任务进行中展示。任务完成、失败、取消或超时后，运行过程不再作为历史消息长期占位；最终结果由 Agent 回答、错误提示或审批状态承载。

## 标准阶段

后端用 `run.stage` 作为前端展示运行过程的统一事件。为了兼容历史事件和调试，仍会写入 `run.queued`、`run.started`、`run.completed` 这些状态事件。

| stage | 状态 | 含义 | 前端标题 |
| --- | --- | --- | --- |
| `queued` | `queued` | 后端已创建任务，等待 worker 领取 | 任务已进入队列 |
| `started` | `running` | worker 已领取任务并开始处理 | 开始处理 |
| `recovering` | `running` | 上一个 worker 租约过期，新 worker 正在恢复任务 | 恢复运行 |
| `retrieving` | `running` | 正在判断并检索知识库 | 检索知识库 |
| `generating` | `running` | 正在组织最终回答 | 生成回答 |
| `low_confidence` | `completed` | 检索发生但没有足够可靠的知识库依据 | 低置信度 |
| `completed` | `completed` | 最终回答已写入消息 | 回答完成 |
| `failed` | `failed` | 运行失败 | 运行失败 |
| `cancelled` | `cancelled` | 用户或系统取消运行 | 已取消 |
| `timed_out` | `timed_out` | 运行超过限制 | 运行超时 |

## RAG 事件

当本次运行判断需要检索知识库时，必须写入 `rag.retrieved` 事件。

事件 payload 至少包含：

- `should_retrieve`
- `mode`
- `reason`
- `raw_hit_count`
- `reliable_hit_count`
- `hit_count`
- `message`
- `citations`
- `reliability_policy`
- `evidence_decision`
- `retrieval_stats`

前端回答下方展示的知识库证据来自最终 assistant message 的 `metadata_json.rag` 和 `metadata_json.citations`。运行过程中的 `rag.retrieved` 用于实时反馈，不替代最终回答证据。

每条 citation 应尽量包含：

- `knowledge_base_name` / `knowledge_base_scope`: 命中的知识库及范围。
- `filename`: 来源文件。
- `document_version` / `current_version`: 命中文档版本和当前发布版本。
- `section_label`: 非 PDF 文档的章节、标题或工作表定位，例如 Markdown 标题或 Excel 工作表。
- `source_page`: PDF 页码；没有页码时前端显示“定位未记录”。
- `score`: 检索或重排分数。
- `chunk_id`: Chunk 标识。
- `preview`: 命中片段预览。

## Prompt 版本

Agent 配置中的 `prompt_version` 是运行时提示词版本号。创建 Agent 时默认是 `v1`；如果修改 `system_prompt` 且没有显式提供新的 `prompt_version`，接口会自动从 `vN` 递增到 `vN+1`。

一次运行开始后，后端会把当时的 `prompt_version` 固定为运行快照，并写入：

- 创建 run 时的用户消息 `metadata_json.prompt_version`。
- `run.stage`、`rag.retrieved`、`approval.required` 等关键运行事件。
- 最终 assistant message 的 `metadata_json.prompt_version`。

这样后续即使 Agent 的 prompt 被修改，历史回答仍然能追溯到当时使用的提示词版本。

## 运行耗时

完成类事件应尽量带上 `run_timing`，用于解释本轮运行慢在哪里：

- `total_run_ms`: 从 worker 开始处理到得到终态的总耗时。
- `generation_ms`: 进入生成阶段后，LangGraph/模型调用/工具链路产生最终回答的耗时。
- `queue_wait_ms`: 任务从创建到被 worker 领取的等待耗时。
- `recovery_lag_ms`: 运行中任务租约过期后，到新 worker 恢复领取之间的滞后耗时。

RAG 检索耗时仍放在 `retrieval_stats` 中；前端会在运行过程里分别展示检索耗时、生成耗时和总耗时。

审批事件应尽量带上 `approval_wait_ms`，表示审批请求从创建到批准或拒绝的等待耗时。审批列表也展示同一字段，方便判断人工卡点。

`reliability_policy` 用来解释为什么某些命中会展示、某些命中会被丢弃：

- `min_score`: 绝对最低可靠分。
- `relative_score_ratio`: 相对最高分比例。
- `top_score`: 本次检索最高分。
- `threshold`: 实际可靠阈值，等于 `max(min_score, top_score * relative_score_ratio)`。
- `max_display_hits`: 前端最多展示的证据条数。
- `max_context_hits`: 送入模型上下文的证据条数。

`evidence_decision` 用来解释本轮证据决策：

- `decision`: `accepted` / `low_confidence` / `no_hits` / `unscored`。
- `raw_hit_count`: 原始召回片段数。
- `scored_hit_count`: 有可用分数的片段数。
- `rejected_hit_count`: 被阈值或上下文限制过滤的片段数。
- `rejected`: 最多展示若干条被过滤片段，包含 `chunk_id`、`filename`、`score` 和 `reason`。

`retrieval_stats` 用来解释检索耗时：

- `retrieval_total_ms`: 本轮检索总耗时。
- `stage_ms`: embed、vector_search、bm25_search、fusion、revalidate、rerank 等阶段耗时。
- `vector_hits` / `bm25_hits` / `fused_hits` / `candidate_hits`: 各阶段候选规模。
- `dropped_by_revalidation`: 版本、租户、删除状态复验时被丢弃的候选数。

## 低置信度拒答

如果策略判断应该检索知识库，但可靠命中为 0，Agent 应返回低置信度拒答，而不是编造答案。

此时要求：

- 写入 `run.stage`，`stage=low_confidence`。
- 最终 assistant message 的 `metadata_json.rag.low_confidence=true`。
- 最终回答说明未找到足够可靠的知识库依据。

## 失败分类

失败、超时和取消类终态事件应带上稳定枚举 `error_category`，并同时带上中文展示字段 `error_category_label`，用于前端解释问题来源，也方便后续按失败类型聚合。

常见分类：

| error_category | error_category_label |
| --- | --- |
| `rag_error` | 知识库检索异常 |
| `model_error` | 模型调用异常 |
| `approval_error` | 审批异常 |
| `tool_error` | 工具调用异常 |
| `state_store_error` | 状态存储异常 |
| `run_timeout` | 运行超时 |
| `cancelled_by_user` | 用户取消 |
| `system_error` | 系统异常 |

## 自动滚动

聊天窗口只在用户接近底部时自动跟随新消息。如果用户向上滚动，前端不得强制拉到底部；用户回到底部后再恢复自动跟随。

## 回归检查

修改 run 事件、RAG 事件或前端事件标题后，运行：

```powershell
python scripts/check_run_events.py
```
