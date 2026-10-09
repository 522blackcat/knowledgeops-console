# AI Agent 项目面试知识点与代码索引

这份文档用于面试前快速复盘本项目的 AI Agent 相关实现。它不是简历话术模板，而是把“能讲的知识点”和“代码证据”对应起来，避免回答时只说概念。

## 0. 项目一句话

KnowledgeOps Console 是一个面向企业知识问答和 Agent 工作流的控制台：用户创建 Agent，配置系统提示词、工具权限和知识库范围；提问后后端创建 AgentRun，worker 基于 LangGraph 执行模型、工具、RAG 检索、人工审批和事件流，最终把回答、证据、运行状态和评测结果落库并展示在前端。

可以讲：

- 这是一个“Agent 编排 + RAG + 工具调用 + 人工审批 + eval 看板”的完整闭环项目。
- 数据库负责权威状态，Redis 负责唤醒和取消通知，Qdrant 负责向量检索，LangGraph 负责 Agent 状态机和 checkpoint。
- RAG 不只是把文档塞给模型，而是包含解析、切片、入库、混合检索、重排、版本复验、低置信度拒答和 eval 验证。

## 1. Agent 创建与配置

核心问题：

- Agent 的配置放在哪里？
- 如何支持不同知识库范围？
- prompt 版本如何追踪？

代码位置：

- [app/agent_api.py](D:/product/knowledgeops-console/app/agent_api.py:53)  
  `ensure_prompt_version()`：创建 Agent 时如果没有 `prompt_version`，默认写 `v1`。
- [app/agent_api.py](D:/product/knowledgeops-console/app/agent_api.py:63)  
  `bump_prompt_version()`：修改 `system_prompt` 时自动递增版本。
- [app/agent_api.py](D:/product/knowledgeops-console/app/agent_api.py:155)  
  `create_agent()`：创建 Agent，写入 `agent_definitions.configuration`。
- [app/agent_api.py](D:/product/knowledgeops-console/app/agent_api.py:233)  
  `update_agent()`：更新 Agent 配置、提示词和知识库策略。
- [infrastructure/models.py](D:/product/knowledgeops-console/infrastructure/models.py:248)  
  `AgentDefinition`：Agent 主表，`configuration` 保存 prompt、工具白名单、知识库范围等配置。

面试讲法：

> Agent 的配置不是写死在代码里，而是存到 `AgentDefinition.configuration`。里面包括 `system_prompt`、`prompt_version`、`allowed_tools`、`knowledge_scope`、`knowledge_base_ids`。这样做的好处是 Agent 可以在线创建和修改，历史运行通过 `prompt_version` 追溯当时使用的提示词版本。

可被追问：

- 为什么要有 `prompt_version`？
- 如果线上回答出问题，怎么追溯当时用的 prompt？
- Agent 关联全局知识库和指定知识库有什么区别？

## 2. AgentRun 创建与幂等

核心问题：

- 用户点击发送后，后端如何创建一次运行？
- 为什么重复提交不会产生重复任务？
- 用户问题如何马上进入会话？

代码位置：

- [app/run_api.py](D:/product/knowledgeops-console/app/run_api.py:82)  
  `create_run()`：创建 AgentRun。
- [app/run_api.py](D:/product/knowledgeops-console/app/run_api.py:141)  
  根据 `idempotency_key` 查重，避免重复提交。
- [app/run_api.py](D:/product/knowledgeops-console/app/run_api.py:195)  
  `append_message()`：把用户问题写入 `conversation_messages`。
- [app/run_api.py](D:/product/knowledgeops-console/app/run_api.py:228)  
  写入 `run.stage` 的 `queued` 事件。
- [infrastructure/models.py](D:/product/knowledgeops-console/infrastructure/models.py:675)  
  `AgentRun`：一次 Agent 运行的状态、租约、checkpoint、错误信息。
- [infrastructure/models.py](D:/product/knowledgeops-console/infrastructure/models.py:388)  
  `ConversationMessage`：用户消息、助手回答、工具消息的持久化表。

面试讲法：

> 用户发送问题后，API 会先写用户消息，再创建 `AgentRun`，并写入 queued 事件。`idempotency_key` 用来处理前端重试或网络抖动，避免同一个请求生成多个运行。真正执行不在 API 线程里做，而是由 agent-worker 领取任务。

可被追问：

- 为什么不在接口里直接调用模型？
- 如果用户重复点发送怎么办？
- 会话消息和运行状态为什么分两张表？

## 3. Worker 领取任务与租约恢复

核心问题：

- 多个 worker 如何抢任务？
- worker 崩溃后任务怎么恢复？
- 超时、取消和失败怎么处理？

代码位置：

- [agent/worker.py](D:/product/knowledgeops-console/agent/worker.py:545)  
  `load_run_context()`：加载 AgentRun、Agent、Conversation、User 上下文。
- [agent/worker.py](D:/product/knowledgeops-console/agent/worker.py:679)  
  `set_run_status()`：统一设置终态，写失败消息和事件。
- [agent/worker.py](D:/product/knowledgeops-console/agent/worker.py:181)  
  `classify_run_error()`：把失败分成 RAG、模型、审批、工具、状态存储、超时等类型。
- [agent/worker.py](D:/product/knowledgeops-console/agent/worker.py:2445)  
  `process_agent_run()`：一次 AgentRun 的执行入口。
- [agent/worker.py](D:/product/knowledgeops-console/agent/worker.py:2627)  
  启动时调用 `setup_checkpoint_tables()`，初始化 LangGraph checkpoint 表。
- [infrastructure/checkpoint.py](D:/product/knowledgeops-console/infrastructure/checkpoint.py:22)  
  PostgreSQL checkpointer。

面试讲法：

> AgentRun 是数据库里的权威状态。worker 通过租约领取任务，执行中维护 heartbeat。如果 worker 崩溃，租约过期后其他 worker 可以恢复。失败时不是简单写 failed，而是带上错误分类，前端事件流能显示是模型异常、RAG 异常、审批异常还是工具异常。

可被追问：

- 任务执行一半 worker 挂了怎么办？
- 为什么要 checkpoint？
- 为什么 Redis 不作为权威状态？

## 4. LangGraph 状态机

核心问题：

- Agent 是怎么循环调用模型和工具的？
- 如何限制工具次数？
- 工具调用结果如何回到模型上下文？

代码位置：

- [agent/graph.py](D:/product/knowledgeops-console/agent/graph.py:89)  
  `build_agent_graph()`：构建 LangGraph。
- [agent/graph.py](D:/product/knowledgeops-console/agent/graph.py:277)  
  `tool_call_count`：统计工具调用次数。
- [agent/graph.py](D:/product/knowledgeops-console/agent/graph.py:315)  
  `tools_node()`：执行模型请求的工具调用。
- [agent/state.py](D:/product/knowledgeops-console/agent/state.py:29)  
  Agent 状态结构，包括 `pending_tool_calls`、`tool_results`、`tool_call_count`。
- [agent/worker.py](D:/product/knowledgeops-console/agent/worker.py:2064)  
  `execute_graph()`：注入上下文、RAG 证据、checkpoint 并运行图。

面试讲法：

> 这个项目没有把 Agent 写成一个无限 while 循环，而是用 LangGraph 把模型节点和工具节点组织成状态机。模型产生 tool calls 后进入 tools 节点，工具执行结果写回 state，再回到模型节点继续生成。`tool_call_count` 和配置里的最大工具次数可以防止模型陷入工具循环。

可被追问：

- LangGraph 和普通链式调用有什么区别？
- tool call 死循环怎么避免？
- checkpoint 在 Agent 状态机里解决什么问题？

## 5. 工具系统与权限校验

核心问题：

- Agent 可以调用哪些工具？
- 为什么不能只相信模型传来的工具名？
- 工具如何保证幂等？

代码位置：

- [tools/registry.py](D:/product/knowledgeops-console/tools/registry.py:44)  
  `ToolDefinition`：工具定义，包括名称、参数 schema、风险等级。
- [tools/registry.py](D:/product/knowledgeops-console/tools/registry.py:81)  
  `ToolRegistry`：工具注册表。
- [tools/registry.py](D:/product/knowledgeops-console/tools/registry.py:136)  
  `openai_tools()`：把本地工具转成模型可用的 tool schema。
- [tools/registry.py](D:/product/knowledgeops-console/tools/registry.py:150)  
  `validate_tool_access()`：执行前再次校验工具权限。
- [tools/execution.py](D:/product/knowledgeops-console/tools/execution.py:75)  
  `execute_tool_once()`：工具执行幂等。
- [tools/builtin.py](D:/product/knowledgeops-console/tools/builtin.py:36)  
  `register_builtin_tools()`：注册内置工具。
- [tools/builtin.py](D:/product/knowledgeops-console/tools/builtin.py:48)  
  `search_knowledge` 工具。
- [tools/mcp_registry.py](D:/product/knowledgeops-console/tools/mcp_registry.py:26)  
  MCP 工具接入。

面试讲法：

> 工具权限不能只靠模型请求时传入的 tools 参数，因为模型输出本身不可信。项目在真正执行工具前还会通过 `validate_tool_access()` 校验工具是否在 Agent 白名单内。工具执行通过 `ToolExecution.idempotency_key` 做幂等，避免重试导致外部副作用重复发生。

可被追问：

- 为什么工具执行需要幂等？
- 如果模型伪造一个未授权工具名怎么办？
- MCP 工具和内置工具如何统一管理？

## 6. 高风险操作与人工审批

核心问题：

- 哪些操作需要人审？
- 审批状态如何让 Agent 暂停？
- 审批通过后如何继续？

代码位置：

- [agent/approval.py](D:/product/knowledgeops-console/agent/approval.py:29)  
  `create_approval_request()`：创建审批请求。
- [agent/approval.py](D:/product/knowledgeops-console/agent/approval.py:104)  
  `get_approval_decision()`：读取审批结果。
- [agent/graph.py](D:/product/knowledgeops-console/agent/graph.py:362)  
  工具需要审批时创建 approval request。
- [app/approval_api.py](D:/product/knowledgeops-console/app/approval_api.py:70)  
  `list_pending_approvals()`：待审批列表。
- [app/approval_api.py](D:/product/knowledgeops-console/app/approval_api.py:125)  
  `decide_approval()`：审批通过或拒绝。
- [app/approval_api.py](D:/product/knowledgeops-console/app/approval_api.py:184)  
  `approval_wait_ms`：审批等待耗时。
- [infrastructure/models.py](D:/product/knowledgeops-console/infrastructure/models.py:904)  
  `ApprovalRequest`：审批请求表。

面试讲法：

> 高风险工具不是让模型直接执行，而是生成审批请求，把 AgentRun 置为 `waiting_approval`。前端可以看到审批卡片，管理员批准或拒绝后写入审批事件和审计日志。这样把自动化能力和人工控制结合起来，避免 Agent 直接执行危险操作。

可被追问：

- 人工审批和普通权限控制有什么区别？
- 如果审批期间 worker 重启怎么办？
- 如何防止审批请求被篡改？

## 7. RAG 入库链路

核心问题：

- 文档上传后如何变成可检索知识？
- 切片怎么做？
- 为什么不会重复入库？

代码位置：

- [app/knowledge_api.py](D:/product/knowledgeops-console/app/knowledge_api.py:1)  
  知识库创建、文档上传、入库任务创建。
- [rag/storage.py](D:/product/knowledgeops-console/rag/storage.py:43)  
  `resolve_storage_path()`：文件存储路径安全处理。
- [rag/parser.py](D:/product/knowledgeops-console/rag/parser.py:25)  
  `ParsedSection`：解析后的结构。
- [rag/parser.py](D:/product/knowledgeops-console/rag/parser.py:68)  
  Markdown 解析。
- [rag/parser.py](D:/product/knowledgeops-console/rag/parser.py:224)  
  PDF 按页解析。
- [rag/parser.py](D:/product/knowledgeops-console/rag/parser.py:257)  
  Word 按标题/段落解析。
- [rag/chunking.py](D:/product/knowledgeops-console/rag/chunking.py:108)  
  `split_sections()`：按 section 和 token 切片。
- [rag/ingest_utils.py](D:/product/knowledgeops-console/rag/ingest_utils.py:25)  
  `stable_chunk_id()`：基于 document、version、chunk_index 生成稳定 chunk id。
- [rag/worker.py](D:/product/knowledgeops-console/rag/worker.py:509)  
  `complete_ingest_job()`：完成入库并发布版本。
- [rag/worker.py](D:/product/knowledgeops-console/rag/worker.py:636)  
  `prune_stale_vectors()`：清理旧版本向量。

面试讲法：

> API 只负责上传文件和创建入库任务，真正解析、切片、embedding、写 Qdrant 由 rag-worker 异步处理。chunk id 是稳定的，包含文档 id、版本号和 chunk_index，所以 worker 重试不会生成重复向量。文档更新通过 `document_version` 和 `current_version` 管理，旧版本不会被检索返回。

可被追问：

- 为什么入库要异步？
- 为什么 chunk id 要稳定？
- PDF、Markdown、Word 的定位信息有什么区别？

## 8. RAG 检索链路

核心问题：

- 查询时是向量检索还是关键词检索？
- reranker 在哪里用？
- 为什么旧文档不会命中？

代码位置：

- [rag/service.py](D:/product/knowledgeops-console/rag/service.py:87)  
  `/internal/rag/retrieve`：内部 RAG 检索服务。
- [rag/retrieval.py](D:/product/knowledgeops-console/rag/retrieval.py:76)  
  `reciprocal_rank_fusion()`：RRF 融合。
- [rag/retrieval.py](D:/product/knowledgeops-console/rag/retrieval.py:115)  
  `hybrid_retrieve()`：混合检索主流程。
- [rag/retrieval.py](D:/product/knowledgeops-console/rag/retrieval.py:281)  
  PostgreSQL 复验 `status == ready`。
- [rag/retrieval.py](D:/product/knowledgeops-console/rag/retrieval.py:282)  
  排除 `deleted_at is not null` 的文档。
- [rag/retrieval.py](D:/product/knowledgeops-console/rag/retrieval.py:283)  
  只接受 `DocumentChunk.document_version == KnowledgeDocument.current_version`。
- [rag/retrieval.py](D:/product/knowledgeops-console/rag/retrieval.py:315)  
  reranker 分支。
- [rag/bm25.py](D:/product/knowledgeops-console/rag/bm25.py:1)  
  BM25 关键词检索。
- [rag/vector_store.py](D:/product/knowledgeops-console/rag/vector_store.py:1)  
  Qdrant collection、向量搜索和删除。

面试讲法：

> 检索不是单纯 vector search，而是 hybrid retrieval。先用 embedding 查 Qdrant，同时用 BM25 做关键词检索，再通过 RRF 融合候选，最后可选 reranker 重排。候选 chunk id 回到 PostgreSQL 做强复验，确保租户、文档状态、删除状态和版本都是正确的。

可被追问：

- 为什么要 hybrid，不只用向量？
- RRF 是解决什么问题？
- 为什么 Qdrant payload 不能作为最终可信来源？

## 9. Agent 如何决定是否检索 RAG

核心问题：

- 是不是每个问题都强制查 RAG？
- Agent 知识库范围如何影响检索？
- 没有可靠命中时如何处理？

代码位置：

- [agent/worker.py](D:/product/knowledgeops-console/agent/worker.py:922)  
  `build_run_registry()`：根据 Agent 配置决定工具和知识库。
- [agent/worker.py](D:/product/knowledgeops-console/agent/worker.py:1279)  
  `decide_retrieval()`：判断本轮是否需要检索。
- [agent/worker.py](D:/product/knowledgeops-console/agent/worker.py:1734)  
  `retrieve_initial_knowledge()`：运行开始阶段预检索知识库。
- [tools/builtin.py](D:/product/knowledgeops-console/tools/builtin.py:48)  
  `search_knowledge`：模型运行中也可以调用知识库搜索工具。
- [infrastructure/config.py](D:/product/knowledgeops-console/infrastructure/config.py:159)  
  `rag_retrieval_url`：拆分后的内部 RAG 服务地址。

面试讲法：

> Agent 的知识库范围来自配置：`none` 不查，`global` 查租户内全局知识库，`custom` 只查指定多个知识库。运行开始会根据问题和配置决定是否预检索，如果 Agent 允许工具调用，模型过程中也可以调用 `search_knowledge`。这样兼顾了确定性检索和模型自主工具调用。

可被追问：

- 强制检索和模型自主判断各有什么利弊？
- 如果 Agent 选了全局知识库，scope 还有什么意义？
- 如何避免查到不属于当前 Agent 的知识库？

## 10. 低置信度拒答与证据展示

核心问题：

- 为什么有命中但仍然拒答？
- 证据分数如何过滤？
- 前端如何证明答案来自知识库？

代码位置：

- [agent/worker.py](D:/product/knowledgeops-console/agent/worker.py:1646)  
  `rag_reliability_policy()`：计算可靠阈值。
- [agent/worker.py](D:/product/knowledgeops-console/agent/worker.py:1487)  
  `explain_rag_evidence()`：解释采用/过滤了哪些证据。
- [agent/worker.py](D:/product/knowledgeops-console/agent/worker.py:2014)  
  `should_decline_for_low_confidence()`：判断是否低置信度拒答。
- [agent/worker.py](D:/product/knowledgeops-console/agent/worker.py:2030)  
  `low_confidence_answer()`：生成拒答文本和修复建议。
- [frontend/src/App.vue](D:/product/knowledgeops-console/frontend/src/App.vue:295)  
  `citations()`：读取 assistant message metadata 里的证据。
- [frontend/src/App.vue](D:/product/knowledgeops-console/frontend/src/App.vue:321)  
  `citationMeta()`：展示知识库、scope、版本和位置。

面试讲法：

> 项目没有把所有召回片段都给用户，而是用 `max(min_score, top_score * relative_ratio)` 算可靠阈值。低于阈值的片段会进入 rejected evidence，用于排查，但不会作为可靠依据。可靠命中为 0 时，Agent 会拒答并提示用户补充文档、调整 Agent 范围或重建索引。

可被追问：

- 为什么不能把低分片段也给模型？
- top score 相对阈值解决什么问题？
- 用户问的问题和知识库不匹配时如何提示？

## 11. 事件流与前端实时展示

核心问题：

- 用户为什么能看到“运行中”的过程？
- 事件流和最终消息是什么关系？
- 为什么最终 run trace 会消失？

代码位置：

- [agent/events.py](D:/product/knowledgeops-console/agent/events.py:1)  
  run event 写入和通知。
- [app/sse_api.py](D:/product/knowledgeops-console/app/sse_api.py:1)  
  SSE 事件读取接口。
- [docs/RUN_EVENTS.md](D:/product/knowledgeops-console/docs/RUN_EVENTS.md:1)  
  run event 产品规范。
- [frontend/src/App.vue](D:/product/knowledgeops-console/frontend/src/App.vue:177)  
  `pushRunEvent()`：解析 SSE 事件。
- [frontend/src/App.vue](D:/product/knowledgeops-console/frontend/src/App.vue:231)  
  `eventTitle()`：事件标题映射。
- [frontend/src/App.vue](D:/product/knowledgeops-console/frontend/src/App.vue:242)  
  `eventDetail()`：事件详情和耗时展示。

面试讲法：

> 事件流用于“运行中可见”，最终答案仍然以 assistant message 落库。前端展示顺序是历史消息、本次用户消息、运行过程、最终回答。运行结束后 run trace 不作为历史消息长期占位，而是最终回答和 metadata 承载结果。

可被追问：

- SSE 和轮询怎么选？
- 如果用户滚动到上面，为什么不能强制滚到底？
- 事件流和数据库最终状态冲突时听谁的？

## 12. 记忆与会话总结

核心问题：

- Agent 如何拿到历史上下文？
- 长会话如何避免上下文无限增长？

代码位置：

- [memory/context.py](D:/product/knowledgeops-console/memory/context.py:1)  
  构建模型上下文。
- [memory/messages.py](D:/product/knowledgeops-console/memory/messages.py:1)  
  消息追加和序号管理。
- [memory/summary.py](D:/product/knowledgeops-console/memory/summary.py:1)  
  会话总结。
- [agent/worker.py](D:/product/knowledgeops-console/agent/worker.py:2508)  
  运行完成后尝试 `maybe_create_summary()`。
- [infrastructure/models.py](D:/product/knowledgeops-console/infrastructure/models.py:486)  
  `ConversationSummary`。

面试讲法：

> 历史消息存在 PostgreSQL，不完全依赖模型上下文。运行时会构建当前消息上下文，完成后再尝试创建会话总结。总结是非关键后处理，失败不会回滚已完成回答。

可被追问：

- 长对话怎么控制 token？
- 总结失败会不会影响回答？
- summary 和原始消息谁是权威？

## 13. 权限、租户隔离与审计

核心问题：

- 不同角色能做什么？
- 多租户数据如何隔离？
- 关键操作如何审计？

代码位置：

- [app/rbac.py](D:/product/knowledgeops-console/app/rbac.py:1)  
  权限枚举和角色权限。
- [app/dependencies.py](D:/product/knowledgeops-console/app/dependencies.py:1)  
  当前用户、租户和权限依赖。
- [app/audit.py](D:/product/knowledgeops-console/app/audit.py:1)  
  审计日志写入。
- [app/audit_api.py](D:/product/knowledgeops-console/app/audit_api.py:1)  
  审计日志查询。
- [docs/RBAC.md](D:/product/knowledgeops-console/docs/RBAC.md:1)  
  权限矩阵说明。
- [scripts/check_rbac_matrix.py](D:/product/knowledgeops-console/scripts/check_rbac_matrix.py:1)  
  RBAC 文档和代码一致性检查。

面试讲法：

> API 层通过依赖注入拿到当前用户和租户，并用权限枚举控制操作。所有关键表查询都带 tenant_id 条件，审批、知识库、Agent 修改等关键操作写审计日志。这样能解释“谁在什么时间对什么资源做了什么”。

可被追问：

- 权限控制是在前端还是后端？
- 审计日志记录哪些字段？
- 多租户隔离只靠前端过滤可以吗？

## 14. RAG Eval 和看板

核心问题：

- 如何证明 RAG 效果？
- eval 评测的是 LLM 答案还是检索质量？
- 失败样本怎么分析？

代码位置：

- [evaluation/rag_eval_cases_v3.json](D:/product/knowledgeops-console/evaluation/rag_eval_cases_v3.json:1)  
  评测用例。
- [evaluation/rag_eval_runner.py](D:/product/knowledgeops-console/evaluation/rag_eval_runner.py:1)  
  离线 runner。
- [evaluation/rag_metrics.py](D:/product/knowledgeops-console/evaluation/rag_metrics.py:1)  
  指标计算。
- [app/rag_eval_api.py](D:/product/knowledgeops-console/app/rag_eval_api.py:384)  
  `list_rag_eval_reports()`：报告看板数据。
- [app/rag_eval_api.py](D:/product/knowledgeops-console/app/rag_eval_api.py:944)  
  `evaluate_rag_case()`：单条复测。
- [app/rag_eval_api.py](D:/product/knowledgeops-console/app/rag_eval_api.py:1014)  
  `evaluate_rag_batch_retest()`：批量复测。
- [app/rag_eval_api.py](D:/product/knowledgeops-console/app/rag_eval_api.py:1105)  
  `evaluate_rag()`：产品内 eval 检索接口。
- [frontend/src/App.vue](D:/product/knowledgeops-console/frontend/src/App.vue:501)  
  eval 看板页面。
- [scripts/check_eval_reports.py](D:/product/knowledgeops-console/scripts/check_eval_reports.py:1)  
  eval 报告结构检查。

面试讲法：

> 这个 eval 重点不是判断 LLM 最终回答对不对，而是验证检索是否把正确 chunk 排在前面。指标包括 pass rate、Hit@1、Hit@3、Hit@5、MRR、rank histogram。失败样本会按原因、scope、文件、分类聚合，并支持单条和批量复测。

可被追问：

- Hit@K 和 MRR 分别代表什么？
- 为什么 eval case 用 JSON？
- 低召回率应该从哪些方向优化？

## 15. 部署与镜像拆分

核心问题：

- 为什么之前镜像很大？
- 哪些服务需要 embedding/reranker 模型？
- 为什么要把 RAG 检索服务化？

代码位置：

- [Dockerfile](D:/product/knowledgeops-console/Dockerfile:1)  
  多 target 镜像：api、agent、lite、full。
- [docker-compose.yml](D:/product/knowledgeops-console/docker-compose.yml:1)  
  服务编排。
- [requirements-api.txt](D:/product/knowledgeops-console/requirements-api.txt:1)  
  API 轻量依赖。
- [requirements-agent.txt](D:/product/knowledgeops-console/requirements-agent.txt:1)  
  agent-worker 轻量依赖。
- [requirements-lite.txt](D:/product/knowledgeops-console/requirements-lite.txt:1)  
  cleanup/eval 轻量依赖。
- [rag/service.py](D:/product/knowledgeops-console/rag/service.py:87)  
  独立 RAG 检索服务。
- [docs/IMAGE_SPLIT.md](D:/product/knowledgeops-console/docs/IMAGE_SPLIT.md:1)  
  镜像拆分说明。

面试讲法：

> embedding 和 reranker 依赖很重，所以不应该让 api、agent-worker、cleanup-worker 都带模型依赖。项目把 RAG 检索拆成 `rag-api`，api 和 agent-worker 通过 `RAG_RETRIEVAL_URL` 调内部服务。这样 api 和 agent-worker 镜像明显变小，模型只集中在 rag-api/rag-worker。

可被追问：

- 服务拆分后有什么代价？
- rag-api 挂了 Agent 怎么降级？
- 为什么 rag-worker 仍然是重镜像？

## 16. 面试官常见追问与安全回答

### Q1：这个项目里 Agent 和普通 RAG 问答有什么区别？

回答要点：

- 普通 RAG 是“问题 -> 检索 -> 生成”。
- 本项目多了 Agent 配置、工具白名单、LangGraph 状态机、人工审批、事件流、运行状态、会话记忆和 eval 看板。
- RAG 是 Agent 的一个能力，不是全部。

### Q2：如何保证 Agent 不乱用工具？

回答要点：

- Agent 配置里有 `allowed_tools`。
- 模型看到的工具 schema 只包含白名单。
- 真正执行前还会 `validate_tool_access()` 二次校验。
- 高风险工具需要人工审批。
- 工具执行用 idempotency key 防止重复副作用。

### Q3：为什么需要低置信度拒答？

回答要点：

- RAG 的风险不是“没召回”，而是“召回了低质量片段还让模型编答案”。
- 项目根据绝对阈值和相对最高分阈值过滤。
- 没有可靠依据时拒答，并给出修复建议。
- 被过滤片段保留在 metadata，方便排查。

### Q4：如何处理文档更新后的过期知识？

回答要点：

- 文档有 `current_version`。
- chunk 有 `document_version`。
- 检索复验要求两者相等。
- 旧版本向量即使还在 Qdrant，也会被 PostgreSQL 复验挡掉。
- 后续 cleanup worker 再物理清理旧向量。

### Q5：这个项目怎么做可观测性？

回答要点：

- `run_events` 记录运行阶段。
- 前端通过 SSE 展示运行过程。
- 失败有 `error_category` 和中文 label。
- RAG metadata 记录命中数、分数、阈值、耗时、被过滤证据。
- eval 看板记录趋势、失败原因、rank 分布和复测历史。

### Q6：如果 RAG 命中率低，你会怎么优化？

回答要点：

- 先看 eval 失败样本，而不是盲调参数。
- 按失败原因区分：无召回、排序靠后、文件/scope 错误、同义词缺失、切片不合理。
- 切片层面：按问题、标题、表格页签保留语义边界。
- 检索层面：hybrid、reranker、同义词、query rewrite。
- 数据层面：补充文档版本、scope、页码和标题定位。

## 17. 建议你明天看代码的顺序

1. [infrastructure/models.py](D:/product/knowledgeops-console/infrastructure/models.py:248)  
   先看 Agent、Run、Message、Approval、Tool、Knowledge 的表。
2. [app/run_api.py](D:/product/knowledgeops-console/app/run_api.py:82)  
   看用户发送问题后怎么创建运行。
3. [agent/worker.py](D:/product/knowledgeops-console/agent/worker.py:2445)  
   看 worker 如何执行一次 run。
4. [agent/graph.py](D:/product/knowledgeops-console/agent/graph.py:89)  
   看 LangGraph 如何组织模型和工具。
5. [tools/registry.py](D:/product/knowledgeops-console/tools/registry.py:44) 和 [tools/execution.py](D:/product/knowledgeops-console/tools/execution.py:75)  
   看工具定义、权限校验和幂等执行。
6. [agent/approval.py](D:/product/knowledgeops-console/agent/approval.py:29) 和 [app/approval_api.py](D:/product/knowledgeops-console/app/approval_api.py:125)  
   看人工审批闭环。
7. [rag/worker.py](D:/product/knowledgeops-console/rag/worker.py:1)  
   看文档入库。
8. [rag/retrieval.py](D:/product/knowledgeops-console/rag/retrieval.py:115)  
   看混合检索和版本复验。
9. [agent/worker.py](D:/product/knowledgeops-console/agent/worker.py:1734)  
   回来看 Agent 如何把 RAG 证据放进回答。
10. [app/rag_eval_api.py](D:/product/knowledgeops-console/app/rag_eval_api.py:384) 和 [evaluation/rag_eval_runner.py](D:/product/knowledgeops-console/evaluation/rag_eval_runner.py:1)  
    看 eval 如何验证。
11. [frontend/src/App.vue](D:/product/knowledgeops-console/frontend/src/App.vue:501)  
    最后看页面如何展示对话、事件流、证据和 eval。

## 18. 需要补充的真实指标

下面这些不要编造，面试前可以自己跑数据补上：

- RAG eval 最近一次通过率、Hit@1、Hit@3、Hit@5、MRR。
- 单次检索平均耗时、P95 耗时。
- 文档入库速度：多少页/多少 chunk 用时多久。
- reranker 开启前后的命中率变化和耗时变化。
- agent-worker 镜像拆分前后体积变化。
- 一次完整 Agent run 的排队耗时、检索耗时、生成耗时。

