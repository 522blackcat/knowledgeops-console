# Docker 镜像拆分说明

当前目标是避免所有后端服务都安装本地 embedding/reranker 模型依赖。

## 镜像分层

| 服务 | Compose target | 是否包含 `sentence-transformers/torch` | 说明 |
| --- | --- | --- | --- |
| `api` | `api` | 否 | Web API、鉴权、审计、知识库管理、报告读取。 |
| `cleanup-worker` | `lite` | 否 | 只做软删除后的向量清理，不做 embedding。 |
| `rag-eval` | `lite` | 否 | HTTP eval runner，不在容器内加载本地模型。 |
| `agent-worker` | `agent` | 否 | 执行 Agent 状态机和 LLM 调用；通过 `RAG_RETRIEVAL_URL` 调内部 RAG 服务。 |
| `rag-api` | `full` | 是 | 内部 RAG 检索服务，负责查询 embedding 和 reranker。 |
| `rag-worker` | `full` | 是 | 文档解析、切片、embedding、写 Qdrant。 |

## 当前结果

`api` 已从 `full` target 切换为 `api` target，避免安装 torch 模型栈。

`agent-worker` 已经不再直接安装本地 embedding/reranker 依赖。Agent 对话中的
`search_knowledge` 通过内部 `rag-api` 服务完成。

如果后续继续演进，应保持这个边界：

1. `rag-worker` 或独立 `rag-api` 暴露内部检索接口。
2. `agent-worker` 通过 HTTP 调用检索接口。
3. `agent-worker` 改用轻量依赖镜像。

这样才能既保留 Agent 的 RAG 能力，又避免 Agent 镜像携带 10GB 模型依赖。

## 检查命令

```powershell
docker compose build api
docker images --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}" |
  Select-String "knowledgeops-console-(api|agent-worker|rag-worker|cleanup-worker|rag-eval)"
```

期望：

- `api` 明显小于 `agent-worker/rag-worker`。
- `cleanup-worker` 和 `rag-eval` 保持轻量。
- `rag-worker` 仍然是重镜像。
