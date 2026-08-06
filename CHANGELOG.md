# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 格式，
版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。
版本单一来源：`backend/__init__.py.__version__`（config 与 pyproject.toml 同源派生）。

## [Unreleased]

### Fixed

- **统一入口成本闭环（G9.1）**：预算改为**每用户计数**（`DAILY_CALL_LIMIT` 即每用户每日
  调用上限），内存/Redis 后端均按 `user_id` 隔离；`unified-chat` 流式端点补齐预算拦截，
  非流式端点补齐预算拦截 + LLM 用量记账（此前两个端点完全绕过成本控制）。
- **三 Agent 用量全记账**：`document_analysis`、`water_expert` 的 LLM 调用接入
  `llm_callbacks` 用量链，与 `knowledge_qa` 一致——任意 Agent / 任意入口的 token 用量
  都落 `llm_usage` 表，成本可见无死角。
- **refresh 轮换原子化（G9.2）**：吊销 refresh token 改为条件 `UPDATE ... AND revoked_at
  IS NULL` + rowcount 判定，作为"是否已被用掉"的权威闸门——并发重放同一 refresh token
  时仅一次成功，其余 401，杜绝轮换双花。
- **SSE 孤儿任务修复（G9.3）**：SSE 生成器在 `finally` 统一取消并回收 orchestrator
  后台任务——客户端断开（`GeneratorExit`，不被 `except Exception` 捕获）不再留下孤儿
  任务继续占用连接、消耗 LLM 额度；`unified-chat` 流式端点同样包成可取消 task。

## [1.1.0] - 2026-08-06

企业级优化发布：在 1.0.0 基础版本之上完成 8 轮工程化改造
（安全加固 → 可观测性 → 成本与检索质量 → 任务与数据层 → 部署运维 → 前端工程化 → 清理与配置收口 → 工程底座升级）。

### Added

- **认证体系**：标准 JWT（access + refresh 轮换 + 登出撤销）+ 登录防爆破（失败锁定窗口）。
- **可观测性**：JSON 结构化日志 + `request_id` 全链路、Prometheus `/metrics`、live/ready 就绪探针。
- **LLM 用量记账**：token/成本折算落库，`/admin/usage` 聚合展示（含流式路径 usage 捕获）。
- **防幻觉**：RAG 回复携带 citation verdict 真实引用核实。
- **检索质量**：Dense+BM25 混合检索 + 精排，评测基线与置信度修复。
- **任务与数据层**：摄取任务持久化队列（崩溃可恢复 + 多 worker + 失败重试）、
  SQLite 连接池、Chroma 同步调用移出事件循环、迁移框架强化（migration_log 审计 + 降级路径）。
- **部署运维**：Docker 生产形态（compose.prod + 多阶段前端镜像）、备份自动化与保留策略、
  限流/预算后端可插拔（内存 ↔ Redis）。
- **前端工程化**：Vitest 单元/组件测试 + CI 接入、全局错误边界、token 单通道。
- **工程底座**：`pyproject.toml` 单一配置源、版本单一来源、标准 `docs/` 分层、本 CHANGELOG。

### Fixed

- LLM 用量记账流式路径不落库：langchain-openai 1.x 仅在默认 OpenAI base_url 下自动开启
  `stream_usage`，自定义 base_url（DashScope）需显式开启并从流式 chunk 的
  `usage_metadata` 捕获（`on_llm_new_token` unwrap `.message`）。
- 检索置信度偏差（评测基线校准）。
- 前端路由加载 / 全局异常兜底。

### Changed

- 依赖冻结 `requirements.lock.txt`，Docker 与 CI 安装路径统一。
- 8 轮 phase 的代码清理与配置收口（G7）：硬编码收敛、死代码移除。
- 工具链配置收口到根 `pyproject.toml`（`[tool.ruff]` / `[tool.pytest.ini_options]`），
  取代散落的 `.ruff.toml` / `pytest.ini`。
- 内部规划文档从公开 `docs/` 移入 `docs/planning/`，README 只链接标准文档。

### Security

- 生产环境密钥 fail-fast 校验（`ensure_secrets`）：缺失 `TOKEN_SECRET` / `DASHSCOPE_API_KEY` 拒绝启动。
- Origin 白名单统一收口（CORS / auth / security 三处同源），跨域伪造请求 403。

### Removed

- 散落配置文件 `.ruff.toml` / `pytest.ini`。
- 52 处 `Reference: §x.x` 死引用 docstring（改写为自描述说明）。
- 根目录空 `chroma/` 孤儿目录。

## [1.0.0] - 2026-07

初始可用版本：水利知识库 RAG 问答、多 Agent（knowledge_qa / document_analysis / water_expert）、
LangGraph 编排、SSE 流式、SQLite + ChromaDB、Vue3 前端、docker-compose 开发栈。
