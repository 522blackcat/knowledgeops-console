# RBAC 权限矩阵

本项目使用租户内角色权限。后端以 `app/rbac.py` 为准，前端只做可用性提示和按钮隐藏，不能替代后端权限校验。

## 角色

| 角色 | 定位 |
| --- | --- |
| `admin` | 租户管理员，拥有全部权限 |
| `operator` | 日常运营人员，可管理 Agent、知识库、审批和运行任务 |
| `viewer` | 只读用户，可查看 Agent、知识库和运行结果 |

## 权限矩阵

| 功能 | admin | operator | viewer |
| --- | --- | --- | --- |
| 查看 Agent | yes | yes | yes |
| 创建 / 修改 Agent | yes | yes | no |
| 发起 Agent 对话 | yes | yes | no |
| 查看运行任务 | yes | yes | yes |
| 取消运行任务 | yes | yes | no |
| 查看知识库 | yes | yes | yes |
| 创建知识库 | yes | yes | no |
| 上传文档 | yes | yes | no |
| 删除文档 | yes | yes | no |
| 查看人工审批 | yes | yes | no |
| 批准 / 拒绝审批 | yes | yes | no |
| 查看审计日志 | yes | no | no |
| 查看管理员工作台 | yes | no | no |
| 管理用户 | yes | no | no |

## 后端权限枚举

| 权限 | 含义 |
| --- | --- |
| `agent:read` | 查看 Agent |
| `agent:write` | 创建或修改 Agent |
| `run:read` | 查看运行任务和对话 |
| `run:create` | 创建运行任务 |
| `run:cancel` | 取消运行任务 |
| `approval:read` | 查看审批请求 |
| `approval:review` | 处理审批请求 |
| `knowledge:read` | 查看知识库和入库任务 |
| `knowledge:write` | 创建知识库、上传或删除文档 |
| `user:read` | 查看用户 |
| `user:manage` | 创建或管理用户 |
| `audit:read` | 查看审计日志 |

## 前端表现

前端根据登录用户角色隐藏或禁用操作：

- `viewer` 在 Agent 页只能查看和进入对话，不显示创建/修改按钮。
- `viewer` 在知识库页只能查看，不显示创建、上传和删除按钮。
- `viewer` 在人工审批页只读，不显示批准/拒绝按钮。
- 只有 `admin` 能看到审计页和管理员工作台入口。

注意：前端隐藏按钮只是体验优化，真正的权限边界必须由后端 `require_permission(...)` 保证。

前端权限开关集中在 `frontend/src/App.vue`：

- `canManageAgents`
- `canManageKnowledge`
- `canReviewApprovals`
- `canCreateRuns`
- `canManageUsers`

## 一致性检查

修改角色、权限枚举或前端权限开关后，运行：

```powershell
python scripts/check_rbac_docs.py
```

这个检查会对比后端 `Permission`、`ROLE_PERMISSIONS`、本文档和前端权限开关，避免文档和代码脱节。

## 审计

以下写操作会写入审计日志：

- `agent.create`
- `agent.update`
- `knowledge_base.create`
- `document.upload`
- `document.upload_duplicate`
- `document.delete`
- `approval.approved`
- `approval.rejected`

审计日志仅 `admin` 可查看。

agent.update 的审计 metadata 会额外包含结构化 diff：

- changes.fields: Agent 顶层字段变更，例如 `name`、`description`、`agent_type`、`status`。
- configuration_diff: Agent 配置关键字段变更，例如 `system_prompt`、`prompt_version`、`knowledge_scope`、`knowledge_base_ids`、`retrieval_mode`、`allowed_tools`。
- changes.changed_keys: 本次变更的字段路径列表。

这样可以追溯谁修改了 prompt、版本号为什么变化，以及 Agent 从全局知识库切换到指定知识库等配置变化。
