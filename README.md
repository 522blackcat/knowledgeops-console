# KnowledgeOps Console

知识运营 Agent 控制台，用于管理 Agent、知识库、RAG 入库、对话运行过程、人工审批和 RAG 检索评测。

项目包含：

- FastAPI 后端 API
- Vue 3 + Element Plus 前端控制台
- PostgreSQL 数据库
- Redis 队列
- Agent Worker
- RAG 入库 Worker
- RAG eval 测试集与报告输出
- BGE embedding / reranker 本地模型缓存预热脚本

## 功能概览

- Agent 管理：创建、修改、启停 Agent。
- 知识库管理：创建知识库、选择范围、上传文档、查看入库进度。
- RAG 检索：支持 BM25、向量检索、hybrid 检索和 reranker。
- 对话控制台：展示用户消息、运行过程、Agent 回复和知识库命中来源。
- 人工审批：高风险操作进入审批队列。
- 管理员工作台：查看用户、队列和主要数据表。
- Eval：提供 50 条 RAG 检索评测用例，输出 JSON、CSV 和 Markdown 报告。

## 运维文档

- 日常启动、迁移、备份和排障：[docs/OPERATIONS.md](docs/OPERATIONS.md)
- 上线前补缺、回归和故障复盘：[docs/PRODUCTION_GAP_RUNBOOK.md](docs/PRODUCTION_GAP_RUNBOOK.md)

## 技术栈

- Python 3.11
- FastAPI
- SQLAlchemy / Alembic
- PostgreSQL 16
- Redis 7
- Vue 3
- Vite
- Element Plus
- sentence-transformers
- jieba
- Qdrant
- Ollama 或兼容 OpenAI API 的云模型

## 目录结构

```text
.
├── agent/                 # Agent 图、运行、审批和事件逻辑
├── alembic/               # 数据库迁移
├── app/                   # FastAPI 路由和 API
├── docs/                  # 运维和部署文档
├── evaluation/            # RAG eval cases、runner 和结果说明
├── frontend/              # Vue 前端
├── infrastructure/        # 配置、数据库、模型客户端、Redis
├── memory/                # 会话记忆
├── rag/                   # 文档解析、切片、检索、入库 worker
├── scripts/               # 运维脚本
├── tools/                 # 内置工具和 MCP 工具注册
├── docker-compose.yml
└── Dockerfile
```

## 环境准备

需要提前安装：

- Docker Desktop
- Python 3.11+
- 可用的 Qdrant 服务
- 可用的 Ollama 服务，或兼容 OpenAI API 的云模型服务

默认端口：

- 前端：`http://localhost:8080`
- 后端 API：`http://localhost:8000`
- API 文档：`http://localhost:8000/docs`

## 首次启动

复制环境变量：

```powershell
Copy-Item .env.example .env
```

编辑 `.env`，至少设置：

```env
POSTGRES_PASSWORD=your_postgres_password
SESSION_SECRET=your_long_random_secret
BOOTSTRAP_ADMIN_PASSWORD=your_admin_password
```

如果 Qdrant 和 Ollama 运行在 Windows 主机，Docker 容器内通常使用：

```env
QDRANT_URL=http://host.docker.internal:6333
OLLAMA_BASE_URL=http://host.docker.internal:11434/v1
```

启动基础服务：

```powershell
docker compose up -d postgres redis
```

构建镜像：

```powershell
docker compose build api frontend agent-worker rag-worker cleanup-worker
```

执行数据库迁移：

```powershell
docker compose run --rm api alembic upgrade head
```

初始化管理员：

```powershell
docker compose run --rm api python -m app.bootstrap
```

启动全部服务：

```powershell
docker compose up -d
```

## 常用命令

查看服务：

```powershell
docker compose ps
```

查看日志：

```powershell
docker compose logs -f api agent-worker rag-worker frontend
```

重建前端：

```powershell
docker compose up -d --build frontend
```

重建后端和 Worker：

```powershell
docker compose up -d --build api agent-worker rag-worker
```

## 运维手册

完整部署、迁移、重建、模型缓存、RAG eval、审计、备份和排障流程见：

```text
docs/OPERATIONS.md
```

角色权限矩阵见：

```text
docs/RBAC.md
```

这份手册更适合按步骤执行；README 只保留快速启动和功能说明。

## RAG 模型预热

项目默认让业务服务优先从本地 Docker 模型缓存加载 embedding / reranker，避免 Worker 在正常运行时反复访问 HuggingFace。

首次使用前建议预热模型：

```powershell
docker compose exec -T api python scripts/warm_rag_models.py
```

如果要使用 reranker：

```powershell
docker compose exec -T api python scripts/warm_rag_models.py --reranker
docker compose exec -T agent-worker python scripts/warm_rag_models.py --reranker
docker compose exec -T rag-worker python scripts/warm_rag_models.py
```

模型缓存使用 Docker volume：

```text
project_hf_models
```

普通重启或重建镜像不会删除已下载模型。

## RAG 检索策略

Agent 默认使用 `RAG_RETRIEVAL_MODE=auto`，不是每轮强制检索：

- 未绑定知识库：不检索。
- 闲聊、助手身份类问题：不检索。
- 明确要求根据知识库、文档、资料、制度或项目回答：检索。
- 其他已绑定知识库的业务/技术问题：检索，避免漏掉项目资料。

检索后会过滤低质量命中：

```env
RAG_MIN_SCORE=0.35
RAG_RELATIVE_SCORE_RATIO=0.45
RAG_CONTEXT_TOP_K=5
RAG_DISPLAY_TOP_K=8
```

含义：

- `RAG_MIN_SCORE`：低于该分数的证据不进入回答上下文，也不展示。
- `RAG_RELATIVE_SCORE_RATIO`：证据分数还必须不低于 `top_score * ratio`。
- `RAG_CONTEXT_TOP_K`：最多喂给模型的可靠证据数。
- `RAG_DISPLAY_TOP_K`：最多展示给用户看的可靠命中数。

如果检索结果都低于阈值，系统会记录“未找到足够可靠的知识库依据”，不会硬塞低质量片段给模型。

## RAG Eval

快速跑一条：

```powershell
python evaluation/rag_eval_runner.py --limit 1 --fail-under 0 --timeout 10
```

完整默认评测使用 lexical 模式：

```powershell
python evaluation/rag_eval_runner.py
```

评测 hybrid 检索：

```powershell
python evaluation/rag_eval_runner.py --retrieval-mode hybrid --timeout 180 --fail-under 0
```

评测 hybrid + reranker：

```powershell
python evaluation/rag_eval_runner.py --retrieval-mode hybrid --use-reranker --timeout 180 --fail-under 0
```

报告输出目录：

```text
evaluation/results/
```

每次运行会生成：

- JSON 明细，含 `summary.rank_metrics`：按锚点真实名次算出的 hit@1/3/5、MRR、
  名次直方图与 miss 用例 ID
- CSV 表格，每条用例一行，含 `anchor_rank` 列
- Markdown 摘要，开头同步列出上述指标

容器里跑要用默认输出目录把报告留在宿主：compose 已把 `./evaluation/results`
挂进 `api` 与 `rag-eval`，不传 `--output-dir` 就会落在仓库的 `evaluation/results/`。
注意 `pass_rate` 判的是"锚点出现在返回的整页结果里"，等价 hit@返回条数，
只看它会藏掉首条精度；`rank_metrics` 才是分块与融合调参的目标数。

## 数据持久化

Compose 已固定 named volumes，避免项目文件夹改名后数据看起来像丢失：

```text
project_postgres_data
project_redis_data
project_hf_models
```

以下操作不会清空数据库：

```powershell
docker compose restart
docker compose up -d
docker compose up -d --build
```

会清空数据的操作包括：

- `docker compose down -v`
- 手动删除 Docker volume
- 在管理端或数据库中手动清表

## 安全说明

- 不要提交 `.env`。
- 不要提交上传文件、日志、评测结果和本地缓存。
- 首次初始化后，应妥善保存或清理 `BOOTSTRAP_ADMIN_PASSWORD`。
- 当前配置适合本机开发部署；公开部署前需要补充 TLS、域名、备份、密钥管理和访问控制。

## Git

本仓库默认分支为：

```text
main
```

初始化后可以添加远程仓库并推送：

```powershell
git remote add origin <your-repository-url>
git push -u origin main
```


