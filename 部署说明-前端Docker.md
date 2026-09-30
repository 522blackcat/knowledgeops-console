# KnowledgeOps Console 部署（Windows PowerShell）

当前项目（知识运营 Agent 控制台）使用仓库根目录的 `docker-compose.yml`。不要再使用旧文档里的
`docker-compose.full.yml`。

## 端口

- 前端页面：`http://localhost:8080`
- FastAPI 后端：`http://localhost:8000`
- RAG eval 默认直连后端 `8000`，不要走前端 nginx 代理 `8080`。

## 首次启动

1. 复制环境变量：

   ```powershell
   Copy-Item .env.example .env
   ```

2. 修改 `.env`：

   - 设置 `POSTGRES_PASSWORD`
   - 设置足够长的 `SESSION_SECRET`
   - 设置至少 12 位的 `BOOTSTRAP_ADMIN_PASSWORD`
   - 如果 Qdrant/Ollama 已映射到 Windows 主机，使用：
     - `QDRANT_URL=http://host.docker.internal:6333`
     - `OLLAMA_BASE_URL=http://host.docker.internal:11434/v1`

3. 启动基础服务：

   ```powershell
   docker compose up -d postgres redis
   ```

4. 构建镜像：

   ```powershell
   docker compose build api frontend agent-worker rag-worker cleanup-worker
   ```

5. 数据库迁移：

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

重建 API：

```powershell
docker compose up -d --build api
```

## RAG Eval

快速跑一条：

```powershell
python evaluation/rag_eval_runner.py --limit 1 --fail-under 0 --timeout 10
```

默认不启用 reranker，以免本机 CPU 和 HuggingFace 冷启动把评测卡超时。
需要把 reranker 也纳入评测时再显式开启：

```powershell
python evaluation/rag_eval_runner.py --limit 1 --use-reranker --timeout 180 --fail-under 0
```

报告会写入：

```text
evaluation/results/
```

## 数据持久化

PostgreSQL 和 Redis 使用 Docker named volumes：

- `postgres_data`
- `redis_data`

普通 `docker compose restart`、`docker compose up -d`、`docker compose up -d --build`
不会清空数据库。只有删除 volume、执行 `docker compose down -v` 或手动清表才会清空数据。

## 安全提醒

`.env` 不要提交到 Git。首次启动完成后，应清除或妥善保管
`BOOTSTRAP_ADMIN_PASSWORD`。当前方案是本机开发部署，不应直接暴露到公网；
公网部署还需要 TLS、域名、备份、密钥管理和访问控制。
