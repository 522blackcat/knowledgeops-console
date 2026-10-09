# KnowledgeOps Console 生产化补缺 Runbook

这份文档用于把本地学习项目推进到“可解释、可回归、可排障”的状态。它不是云生产架构方案，重点是避免本项目里已经踩过的问题反复出现。

## 1. 上线前固定检查

先确认服务已经启动：

```powershell
docker compose ps
python scripts/check_runtime_connectivity.py
```

不启动模型、不写业务数据的静态检查：

```powershell
python scripts/check_release_readiness.py
```

真实 Agent / RAG / 审批回归：

```powershell
python scripts/check_agent_e2e.py --timeout 120
```

真实 RBAC 回归：

```powershell
python scripts/check_rbac_api.py
```

RAG 评测 smoke：

```powershell
python evaluation/rag_eval_runner.py --limit 1 --retrieval-mode lexical --fail-under 0 --timeout 30
```

## 2. 代码改动后怎么重启

改 API、权限、审计、Agent 配置接口：

```powershell
docker compose up -d --build api
python scripts/check_runtime_connectivity.py
```

改 Agent 执行、事件流、审批、RAG 引用：

```powershell
docker compose up -d --build agent-worker
python scripts/check_agent_e2e.py --timeout 120
```

改文档解析、切片、tokenizer、入库 worker：

```powershell
docker compose up -d --build rag-worker cleanup-worker
docker compose exec -T api python scripts/reindex_documents.py --all-ready --dry-run
```

改前端：

```powershell
docker compose build frontend
docker compose up -d frontend
```

改数据库模型或新增表字段：

```powershell
docker compose up -d --build api
docker compose exec -T api alembic upgrade head
```

## 3. RAG 质量调优流程

先跑快速评测，确认接口通：

```powershell
python evaluation/rag_eval_runner.py --limit 1 --fail-under 0 --timeout 30
```

再跑正式评测：

```powershell
docker compose --profile eval run --rm rag-eval
```

看报告：

```text
evaluation/results/
```

优先看这些指标：

- `hit@1`: 第一条是否命中。
- `hit@3` / `hit@5`: 前几条召回质量。
- `MRR`: 命中排名是否靠前。
- `failures`: 失败原因。
- `top_results`: 每个失败样本实际召回了什么。

如果改了切片或入库逻辑，旧文档不会自动改变。先预览重建：

```powershell
docker compose exec -T api python scripts/reindex_documents.py --all-ready --dry-run
```

确认后再执行：

```powershell
docker compose exec -T api python scripts/reindex_documents.py --all-ready
```

## 4. Prompt 和审计追溯

Agent 配置中的 `prompt_version` 用于追溯回答来自哪版提示词。

创建 Agent 时默认：

```json
{"prompt_version": "v1"}
```

通过 API 修改 `system_prompt` 时，如果没有显式传新版本，后端会自动递增：

```text
v1 -> v2 -> v3
```

审计日志里查看：

```http
GET /api/audit/logs?action=agent.update
```

重点看：

- `metadata_json.configuration_diff.system_prompt`
- `metadata_json.configuration_diff.prompt_version`
- `metadata_json.configuration_diff.knowledge_scope`
- `metadata_json.changes.changed_keys`

## 5. 常见故障处理

### API / 前端刚重建后短暂断连

先等启动完成，再检查：

```powershell
docker compose logs --tail=80 api
python scripts/check_runtime_connectivity.py
```

### checkpoint 表不存在

如果日志出现 `relation "checkpoints" does not exist`：

```powershell
docker compose up -d --build agent-worker
docker compose logs -f agent-worker
```

当前 worker 启动时会初始化 checkpoint 表。

### HuggingFace 请求失败

先预热模型：

```powershell
docker compose exec -T api python scripts/warm_rag_models.py
docker compose exec -T api python scripts/warm_rag_models.py --reranker
```

如果模型已经缓存，`.env` 使用：

```env
RAG_MODEL_LOCAL_FILES_ONLY=true
```

### Qdrant 不通

```powershell
docker compose exec -T api python -c "import httpx; print(httpx.get('http://qdrant:6333/collections', timeout=5).status_code)"
```

### Ollama 不通

```powershell
python scripts/check_runtime_connectivity.py
```

看输出里的 Ollama tags URL 和 status。

### 评测太慢

先用 lexical：

```powershell
python evaluation/rag_eval_runner.py --limit 1 --retrieval-mode lexical --fail-under 0 --timeout 30
```

hybrid + reranker 首次加载模型会慢：

```powershell
python evaluation/rag_eval_runner.py --retrieval-mode hybrid --use-reranker --timeout 180 --fail-under 0
```

## 6. 数据安全

不会清数据：

```powershell
docker compose restart
docker compose up -d
docker compose up -d --build
```

会清数据：

```powershell
docker compose down -v
```

备份数据库：

```powershell
docker compose exec -T postgres pg_dump -U agent -d agent_db > backup_agent_db.sql
```

恢复数据库：

```powershell
Get-Content backup_agent_db.sql | docker compose exec -T postgres psql -U agent -d agent_db
```

还需要备份：

- `uploads/`
- `data/`
- `evaluation/results/`
- Docker volume `project_hf_models`

## 7. 当前仍未补齐的生产能力

- 前端文档版本历史和重建索引按钮。
- RAG 页码展示增强：当前 citation 已支持 `source_page`，但非 PDF 或解析不到页码的文档可能为空。后续前端应明确展示“页码未记录”，并继续增强 docx/md 的章节定位信息。
- 更完整的性能指标沉淀，例如检索耗时、reranker 耗时、LLM 耗时。
- 镜像体积继续拆分：当前 `cleanup-worker` 和 `rag-eval` 已使用轻量镜像，`api` / `agent-worker` / `rag-worker` 仍因直接依赖 RAG embedding/reranker 保持完整镜像。后续如果把检索封装成独立 RAG 服务，`api` 和 `agent-worker` 可以降为轻量镜像。
- API 速率限制。
- 更细粒度的工具权限策略。
- 正式部署的 TLS、域名、密钥托管和备份自动化。
