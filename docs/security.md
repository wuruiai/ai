# 安全设计

## 认证模型

标准 JWT 双 token 体系（`backend/api/v1/auth.py`）：

- **access token**：短时效（默认 30 分钟，`ACCESS_TOKEN_TTL_S`），API 鉴权用。
- **refresh token**：长时效（默认 7 天，`REFRESH_TOKEN_TTL_S`），**轮换 + 可吊销**（登出撤销）。
- 注册首个用户自动成为管理员（RBAC：`admin` / `user` 角色，管理端点校验角色）。

### 登录防爆破

同一 username/IP 连续失败超过 `LOGIN_MAX_FAILURES`（默认 5）次进入 `LOGIN_LOCKOUT_S`
（默认 900 秒）锁定，阻止密码猜测。

## 密钥与配置

- `.env` 全程 gitignored（`.gitignore` 含 `.env.*`，仅保留 `.env.example` 模板），任何 secrets 不入库。
- **非本地 fail-fast**：`APP_ENV != local`（production / staging 等）时 `ensure_secrets()`
  强制校验 `TOKEN_SECRET` 与 `DASHSCOPE_API_KEY`，缺失直接拒绝启动。
- 本地开发留空 `TOKEN_SECRET` 时使用进程级随机值（重启后旧 token 失效，仅限开发便利）。

## 数据隔离

所有业务查询按 `user_id` 过滤（消息、文档、thread、用量、审计）——多租户数据互不可见。

## 跨域与请求防护

- **Origin 白名单**：CORS / auth / security 三处统一从 `settings.allowed_origins`
  （`FRONTEND_ORIGIN` + `EXTRA_ALLOWED_ORIGINS`）取值，非法 Origin 请求返回 403。
- **限流**：每用户每分钟 `RATE_LIMIT_PER_MINUTE` 次（chat/upload 等重端点），
  日预算 `DAILY_CALL_LIMIT` 控制调用成本。
- **路径穿越防护**：文档下载/上传路径校验，防越权访问文件系统。
- **SQL 注入防护**：全链路参数绑定，无字符串拼接 SQL。

## 审计与可观测

- 审计日志（`backend/core/audit.py`）：敏感操作（登录、上传、删除、管理操作）落库可查。
- `/admin/audit` 提供管理员审计查询与导出。
- JSON 结构化日志 + `request_id` 全链路，便于安全事件回溯。

## LLM 用量审计

每次 LLM 调用 token/成本记账到 `llm_usage` 表（`/admin/usage` 聚合），
超额（`DAILY_CALL_LIMIT`）可触发预算拦截——成本可见、可控。

## 相关文档

- [架构](architecture.md)
- [部署与运维](deployment.md)
