# KnowledgeOps Console 运维手册

这份文档用于本地或内网部署时的日常操作。它不是云厂商生产部署方案，但覆盖当前项目的关键运维动作：启动、迁移、重建、模型缓存、RAG eval、审计、备份和排障。

如果你在做上线前补缺或排障复盘，也看：

```text
docs/PRODUCTION_GAP_RUNBOOK.md
```

## 服务清单

| 服务 | 作用 | 端口/入口 |
| --- | --- | --- |
| `frontend` | Vue 控制台 | `http://localhost:8080` |
| `api` | FastAPI 后端 | `http://localhost:8000` |
| `postgres` | 业务数据库 | Compose 内部 |
| `redis` | 队列通知与取消标记 | Compose 内部 |
| `agent-worker` | Agent 任务执行 | 后台服务 |
| `rag-worker` | 文档解析、切片、向量化 | 后台服务 |
| `cleanup-worker` | 已删除文档向量清理 | 后台服务 |
| `rag-eval` | RAG 评测任务 | `--profile eval` |

## 首次部署

1. 复制配置：

```powershell
Copy-Item .env.example .env
```

2. 修改 `.env` 中至少这些值：

```env
POSTGRES_PASSWORD=your_postgres_password
SESSION_SECRET=your_long_random_secret
BOOTSTRAP_ADMIN_PASSWORD=your_admin_password
```

3. 启动数据库和 Redis：

```powershell
docker compose up -d postgres redis
```

4. 构建镜像：

```powershell
docker compose build api frontend agent-worker rag-worker cleanup-worker
```

5. 执行迁移：

```powershell
docker compose run --rm api alembic upgrade head
```

6. 初始化管理员：

```powershell
docker compose run --rm api python -m app.bootstrap
```

7. 启动全部服务：

```powershell
docker compose up -d
```

## 日常启动与重建

查看状态：

```powershell
docker compose ps
```

启动或重启全部服务：

```powershell
docker compose up -d
```

只重建前端：

```powershell
docker compose up -d --build frontend
```

改了 API 或数据库模型后：

```powershell
docker compose up -d --build api
docker compose exec -T api alembic upgrade head
```

改了 Agent 执行逻辑后：

```powershell
docker compose up -d --build agent-worker
```

上线前做一次真实 Agent/RAG 回归：

```powershell
python scripts/check_agent_e2e.py --timeout 120
```

这个检查会真实登录、创建会话、发送三类问题并验证：

- 普通 RAG 问题有用户消息、运行事件、最终回答和知识库证据。
- 低置信度问题会拒答，并带 `rag.low_confidence=true`。
- 高风险操作会进入人工审批，并自动拒绝该测试审批项做清理。

上线前做一次真实 RBAC API 回归：

```powershell
python scripts/check_rbac_api.py
```

这个检查会创建或复用两个测试用户：

- `rbac_operator_smoke`
- `rbac_viewer_smoke`

并验证：

- `admin` 可以读审计日志。
- `operator` 不能读审计日志，但可以创建 Agent、查看审批。
- `viewer` 可以读 Agent 和知识库，但不能创建 Agent、不能查看审批。

改了 RAG 入库、切片或向量逻辑后：

```powershell
docker compose up -d --build rag-worker cleanup-worker
```

## 数据迁移

迁移命令：

```powershell
docker compose exec -T api alembic upgrade head
```

查看当前版本：

```powershell
docker compose exec -T api alembic current
```

注意：

- 新增表或字段后必须跑迁移。
- 普通 `docker compose up -d --build` 不会自动执行 Alembic。
- 当前项目已包含审计表迁移 `0003_add_audit_logs`。

如果接口报错提示 `relation "audit_logs" does not exist`，说明镜像已更新但数据库迁移还没执行：

```powershell
docker compose exec -T api alembic upgrade head
```

## 外部依赖检查

Qdrant 用于向量检索。当前 Compose 默认把服务地址设为：

```env
QDRANT_URL=http://qdrant:6333
```

检查 API 容器能否访问 Qdrant：

```powershell
docker compose exec -T api python -c "import httpx; print(httpx.get('http://qdrant:6333/collections', timeout=5).status_code)"
```

模型服务可以是 Ollama 或兼容 OpenAI API 的服务。检查 API 容器里的模型配置：

```powershell
docker compose exec -T api python -c "from infrastructure.config import get_settings; s=get_settings(); print(s.model_provider, s.openai_base_url, s.ollama_base_url)"
```

## 模型缓存

embedding 和 reranker 使用 Docker volume：

```text
project_hf_models
```

预热 embedding：

```powershell
docker compose exec -T api python scripts/warm_rag_models.py
docker compose exec -T rag-worker python scripts/warm_rag_models.py
```

预热 reranker：

```powershell
docker compose exec -T api python scripts/warm_rag_models.py --reranker
docker compose exec -T agent-worker python scripts/warm_rag_models.py --reranker
```

如果日志里出现 HuggingFace 连接失败，优先确认：

- 本机网络是否能访问 HuggingFace。
- `project_hf_models` 是否已经有模型缓存。
- `.env` 中 `RAG_MODEL_LOCAL_FILES_ONLY` 是否设为 `true`。

## RAG 入库检查

上传文档后，看前端“知识库”页的入库任务。

也可以看日志：

```powershell
docker compose logs -f rag-worker
```

常见状态：

| 状态 | 含义 |
| --- | --- |
| `queued` | 等待 worker 领取 |
| `processing` | 正在解析/切片/向量化 |
| `completed` | 入库完成 |
| `failed` | 入库失败 |
| `cancelled` | 被删除或替换时取消 |

### 回填旧分片 Token 数

新上传文档会在 `document_chunks.token_count` 写入真实 Token 数。
如果旧文档是在这个逻辑补齐前入库的，数据库里可能仍是 `0`。
这不会改变向量和文档内容，但会影响分片统计和运维排查。

先预览会更新多少条：

```powershell
docker compose exec -T api python scripts/backfill_chunk_token_count.py --dry-run
```

确认后执行：

```powershell
docker compose exec -T api python scripts/backfill_chunk_token_count.py
```

### 重建文档索引

如果改了切片、tokenizer、reranker 前置策略，旧文档不会自动重新入库。
可以用现有文件创建下一版本入库任务，让 worker 重新解析、切片、向量化。
新版本完成前，旧版本仍保持可检索；新版本完成后才切换 `current_version`。

控制台操作：

1. 打开“知识库”页。
2. 在“上传文档”下方任务表中找到文档。
3. 点击“重建”，系统会调用 `POST /api/knowledge/documents/{document_id}/reindex` 排入新版本入库任务。

如果该文档已有 `queued` / `processing` 任务，接口会拒绝重复重建。

先预览指定文档：

```powershell
docker compose exec -T api python scripts/reindex_documents.py --document-id <document_id> --dry-run
```

确认后执行：

```powershell
docker compose exec -T api python scripts/reindex_documents.py --document-id <document_id>
```

预览所有 ready 文档：

```powershell
docker compose exec -T api python scripts/reindex_documents.py --all-ready --dry-run
```

批量重建全部 ready 文档：

```powershell
docker compose exec -T api python scripts/reindex_documents.py --all-ready
```

脚本会跳过：

- 已删除文档。
- 当前已有 `queued` / `processing` 入库任务的文档。
- 服务端原文件不存在的文档。

## RAG Eval

快速 smoke：

```powershell
python evaluation/rag_eval_runner.py --limit 1 --fail-under 0 --timeout 30
```

容器内正式评测：

```powershell
docker compose --profile eval run --rm rag-eval
```

报告落在：

```text
evaluation/results/
```

前端左侧 `≋` 可查看：

- 通过率
- Hit@1 / Hit@3 / Hit@5
- MRR
- Rank 分布
- 失败原因
- 全部用例明细

检查用例集和报告结构是否能支撑看板明细：

```powershell
python scripts/check_eval_reports.py
```

## Agent Run 事件流

聊天窗口的运行过程规范见：

```text
docs/RUN_EVENTS.md
```

修改 Agent run 状态、事件流或前端事件标题后，运行：

```powershell
python scripts/check_run_events.py
```

## 审计日志

前端左侧管理员区域 `§` 进入审计页。

当前会记录：

- 创建 Agent
- 修改 Agent
- 创建知识库
- 上传文档
- 重复上传文档
- 删除文档
- 审批通过/拒绝

接口：

```http
GET /api/audit/logs
```

支持筛选：

```http
GET /api/audit/logs?action=agent.create
GET /api/audit/logs?resource_type=knowledge_document
GET /api/audit/logs?actor=admin
```

检查审计动作清单、写入点、迁移和接口权限是否一致：

```powershell
python scripts/check_audit_contract.py
```

## 角色权限

角色权限矩阵见：

```text
docs/RBAC.md
```

后端权限以 `app/rbac.py` 和各 API 的 `require_permission(...)` 为准。前端隐藏按钮只是体验优化，不能替代后端校验。

修改角色、权限枚举或前端权限开关后，先跑一致性检查：

```powershell
python scripts/check_rbac_matrix.py
python scripts/check_rbac_docs.py
```

不启动服务的本地上线前检查：

```powershell
python scripts/check_release_readiness.py
```

新增或修改 API 路由后，检查业务接口是否都声明了权限：

```powershell
python scripts/check_api_permissions.py
```

## 备份与恢复

备份 PostgreSQL：

```powershell
docker compose exec -T postgres pg_dump -U agent -d agent_db > backup_agent_db.sql
```

恢复 PostgreSQL：

```powershell
Get-Content backup_agent_db.sql | docker compose exec -T postgres psql -U agent -d agent_db
```

需要额外备份：

- `uploads/`
- `data/`
- `evaluation/results/`
- Docker volume `project_hf_models`，如果不想重新下载模型

不要执行：

```powershell
docker compose down -v
```

除非你明确要删除数据库、Redis 和模型缓存。

## 健康检查

API：

```powershell
curl http://localhost:8000/health/live
```

服务：

```powershell
docker compose ps
```

日志：

```powershell
docker compose logs -f api agent-worker rag-worker frontend
```

本地服务启动后的一键连通性检查：

```powershell
python scripts/check_runtime_connectivity.py
```

这个检查会验证 API、前端、登录、审计、eval 报告，以及 agent-worker 到 Ollama 的连接。

## 常见故障

### 页面 502 或接口 502

通常是 API 还没启动完成，或访问了错误端口。

检查：

```powershell
docker compose ps api frontend
curl http://localhost:8000/health/live
```

RAG eval 要打 `8000`，不是 `8080`。

### `relation "checkpoints" does not exist`

说明 Agent checkpoint 表没有初始化，或 worker 用的是旧镜像。

处理：

```powershell
docker compose up -d --build agent-worker
docker compose logs -f agent-worker
```

当前 worker 启动时会自动创建 checkpoint 表。

### 入库分片数量没有变化

可能是旧镜像或旧 worker 仍在运行。

处理：

```powershell
docker compose up -d --build rag-worker
docker compose logs -f rag-worker
```

已入库旧文档不会自动重新切片。需要删除旧文档后重新上传，或提供专门的重建索引流程。

### Eval 超时

优先用 lexical smoke：

```powershell
python evaluation/rag_eval_runner.py --limit 1 --retrieval-mode lexical --fail-under 0 --timeout 30
```

hybrid / reranker 首次运行需要加载模型，超时可调大：

```powershell
python evaluation/rag_eval_runner.py --retrieval-mode hybrid --use-reranker --timeout 180 --fail-under 0
```

### HuggingFace 连接失败

如果模型已缓存，优先让服务只读本地缓存。

检查 `.env`：

```env
RAG_MODEL_LOCAL_FILES_ONLY=true
```

如果还没缓存，需要先解决网络或手动预热模型。

## 上线前检查清单

- `.env` 不提交 Git。
- `SESSION_SECRET` 使用强随机值。
- `BOOTSTRAP_ADMIN_PASSWORD` 已替换为强密码。
- 已执行 `alembic upgrade head`。
- `docker compose ps` 所有核心服务正常。
- `http://localhost:8000/health/live` 返回 `200`。
- 能创建 Agent。
- 能创建知识库并上传文档。
- RAG eval 至少跑过 smoke。
- RBAC 角色矩阵检查通过：`python scripts/check_rbac_matrix.py`。
- 权限文档一致性检查通过：`python scripts/check_rbac_docs.py`。
- API 权限覆盖检查通过：`python scripts/check_api_permissions.py`。
- Agent run 事件流检查通过：`python scripts/check_run_events.py`。
- RAG eval 报告结构检查通过：`python scripts/check_eval_reports.py`。
- 审计契约检查通过：`python scripts/check_audit_contract.py`。
- 本地上线前检查通过：`python scripts/check_release_readiness.py`。
- 审计页能看到关键操作。
- 已确认备份方式。
