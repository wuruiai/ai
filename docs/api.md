# API 契约

所有业务端点位于 `/api/v1`，均需 `Authorization: Bearer <token>`；`/health` 无需鉴权。

## 概览

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| POST | `/api/v1/auth/register` | 公开 | 注册（首个用户自动成为管理员） |
| POST | `/api/v1/auth/login` | 公开 | 登录 → token |
| GET | `/api/v1/auth/me` | 用户 | 当前用户信息 |
| POST | `/api/v1/auth/change-password` | 用户 | 修改密码 |
| POST | `/api/v1/chat/stream` | 用户 | SSE 流式问答（知识库快路径，带引用核实） |
| POST | `/api/v1/unified-chat/stream` | 用户 | 统一 Agent 入口（流式） |
| POST | `/api/v1/unified-chat/` | 用户 | 统一 Agent 入口（非流式） |
| GET/POST | `/api/v1/documents/` | 用户 | 文档列表 / 上传（异步摄取） |
| GET/PATCH/DELETE | `/api/v1/documents/{id}` | 用户 | 文档详情 / 元数据 / 删除 |
| GET | `/api/v1/threads/` | 用户 | 会话列表 |
| GET/DELETE | `/api/v1/threads/{id}/...` | 用户 | 会话消息 / 删除 |
| POST | `/api/v1/feedback/` | 用户 | 消息反馈（helpful/not_helpful） |
| GET | `/api/v1/admin/*` | 管理员 | 统计 / 用户管理 / 审计 / 导出 |
| GET | `/health` | 公开 | 健康检查 |

## 双聊天端点设计意图

系统提供两个聊天入口，职责刻意分离：

| 端点 | 定位 | 特征 |
|------|------|------|
| `POST /chat/stream` | **知识库问答快路径**（前端 Chat 页使用） | 固定 `knowledge_qa`；检索 → Rerank → 生成 → **citation verdict** 防幻觉；SSE 事件含 `citation_verdict` |
| `POST /unified-chat/stream` | **多 Agent 统一入口**（程序化/未来扩展） | `agent_type` 参数路由（knowledge_qa / document_analysis / water_expert）；支持 `pipeline_mode`、`context` 注入、`pipeline_key` |

选型建议：面向最终用户的聊天走 `chat/stream`（防幻觉与交互完整）；
面向 Agent 编排、多轮 pipeline、非知识问答场景走 `unified-chat`。
两者共享同一 Orchestrator 与检索管线，行为差异只在于入口契约。

## 统一聊天请求（unified-chat）

```json
{
  "message": "汛期水位超限怎么办？",
  "agent_type": "knowledge_qa",
  "session_id": "default",
  "context": {},
  "pipeline_mode": false,
  "pipeline_key": null
}
```

- `agent_type`：`knowledge_qa`（默认）/ `document_analysis` / `water_expert`。
- `session_id`：会话 ID，服务端按用户隔离加载最近 6 条历史做多轮记忆。

## SSE 响应格式

`Content-Type: text/event-stream; charset=utf-8`，事件契约见 [架构](architecture.md#sse-事件契约)。

```text
event: start
data: {"thread_id": "default"}

event: status
data: {"phase": "processing"}

event: token
data: {"delta": "汛期管理"}

event: citation_verdict   # 仅 chat/stream
data: {"verdict": {...}}

event: done
data: {"message_id": "..."}
```

## 错误约定

- 认证失败：`401`；权限不足：`403`；资源不存在：`404`；参数非法：`422`。
- 限流触发：`429`；登录防爆破锁定：`429` + 错误体说明锁定时长。
- 非法 Origin（跨域伪造）：`403`。
- 服务端异常：`500`，统一错误体 `{ "detail": ... }`。

## 相关文档

- [架构](architecture.md)
- [开发指南](development.md)
