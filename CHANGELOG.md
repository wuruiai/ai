# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 格式，
版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。
版本单一来源：`backend/__init__.py.__version__`（config 与 pyproject.toml 同源派生）。

## [Unreleased]

### Added

- **LLM 调用指标（G10.9 M10）**：Prometheus `/metrics` 新增按 model 分维度的
  `llm_calls_total` / `llm_tokens_total{kind=input|output}` / `llm_call_duration_seconds`
  / `llm_cost_cny_total`——`UsageCollector` 逐 LLM 调用打点（`on_llm_start` 记延迟起点，
  `on_llm_end` 落点），与 `llm_usage` 明细表互补，Grafana 可直接看 LLM 调用量、token 消耗、
  延迟与成本趋势；异常/降级调用（无 usage）也记次数与延迟。4 个回归测试。
- **日志统一（G10.9 M11）**：uvicorn 自身日志（启动/生命周期）并入根 JSON logger，其默认
  文本 access log 关闭——HTTP 中间件改输出**结构化** access 日志（`method`/`path`/`status`/
  `duration_ms`/`client` 字段，经 `X-Forwarded-For` 解析真实客户端 IP），Loki/ELK 直接可
  聚合，告别"应用 JSON + uvicorn 文本"两种格式混排。3 个回归测试。
- **前端版本对齐后端（G10.9 M12）**：前端侧边栏品牌区展示后端版本徽标 `vX.Y.Z`，从
  `/health`（版本单一来源 `backend.__version__`）拉取，后端不可达时静默隐藏不阻塞页面——
  前端部署版本与后端始终一致可核对。3 个前端测试。

### Fixed

- **任务队列入队原子化 + 租约续期（G10.8）**：① 入队从「SELECT 查重 → INSERT」两步改为单条
  `INSERT ... SELECT ... WHERE NOT EXISTS` 原子语句——并发上传同一文档不再双插两条 pending
  被两个 worker 抢走双重摄取（`enqueue` 返回是否真正插入，幂等可判）；② 运行期租约心跳——
  摄取任务在执行期间每 `LEASE//3` 秒前移 `claimed_at`，长任务不被误判为「worker 已死」而
  重抢（双重摄取）；③ worker 循环内周期性回收超租约的 `running` 任务回队 pending——此前
  `recover_stale_tasks` 只在启动执行，worker 崩溃但进程未重启时残留任务永久卡住，文档永不
  ready，现在运行中自愈。3 个回归测试。
- **备份调度落地（G10.4）**：`docker-compose.prod.yml` 新增 `backup` sidecar 服务，与
  backend 共用 `water_data` 卷，循环模式每 `BACKUP_INTERVAL_HOURS`（默认 24）小时把
  DB(+WAL) + chroma 备份到卷内 `data/backups/`（保留 `BACKUP_RETENTION_DAYS` 天）——
  此前 `BACKUP_ENABLED=true` 但没有任何服务实际调度，`up -d` 后备份永不执行。
- **前端 SSE 健壮性（G10.3）**：流式 POST 遇到 401 时经 auth store 单飞 refresh 换新
  token 自动重放一次（此前裸 fetch 不走 axios 拦截器，30 分钟会话中途流式被 401 打断）；
  流式中断（网络抖动）在「未产出任何内容」时指数退避自动重连、复用同一助手消息，
  已产出内容或服务端错误不重试，避免半截回答拼接错乱。
- **CI 镜像构建闭环（G10.2）**：新增 `build-images` CI job，构建 backend/frontend 镜像
  并打版本 + 短 sha 不可变 tag；master + 配置 `GHCR_PAT` secret 时推送 GHCR，未配置时
  退化为「镜像能构建」验证——补齐生产 compose「CI 负责构建并推送镜像」此前缺失的环节。
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
- **幽灵向量竞态修复（G9.4）**：摄取 INDEXING 与文档删除共享一个
  `document_write_lock`（`asyncio.Lock`）互斥——删除的"清向量 → 删 DB 行"不会插入到
  摄取的向量写入中间；摄取在锁内重查文档仍存在，删除一旦提交则中止写入并回滚。
  并发删除不再产生"DB 已删、向量残留"的幽灵结果。

### Security

- **容器最小权限 + nginx 加固（G10.10）**：① 后端镜像新增专用 `appuser`（uid 1000）并以
  `USER appuser` 运行，数据/日志目录归其所有（命名卷首挂载自动继承属主，bind mount 见
  deployment.md 说明）——此前整个后端进程以 root 运行；② 前端 nginx 同样非 root：主进程以
  `nginx` 用户运行、绕过需要写 `/etc` 的默认 entrypoint 脚本、仅监听非特权端口 8080
  （`FRONTEND_PORT` 可覆盖），pid/缓存/日志目录预先归 nginx 所有；③ nginx 加固——隐藏
  版本号（`server_tokens off`）、gzip 压缩（静态资源 + API JSON）、`/assets/` 带 hash
  构建产物强缓存 immutable、`index.html` 不缓存（前端版本随时对齐后端）、安全响应头
  （CSP / `X-Content-Type-Options` / `X-Frame-Options` / `Referrer-Policy` /
  `Permissions-Policy`，含错误响应）。已实容器验证：后端进程 uid=1000 可写数据并启动，
  前端进程 uid=101 正常伺服且响应头齐全。
- **成本记账补全 + 失败路径对齐 + 禁用文档生效（G10.7）**：① 检索链路上的辅助 LLM
  （查询分类器 / HyDE / 多查询改写）接入 `llm_callbacks` 用量链——此前这三类调用创建 LLM
  时不带回调，token 消耗是 llm_usage 的成本黑洞；② 预算 `record_call` 移入流式端点
  finally（成功/崩溃/客户端断开统一收口），失败路径不再绕过每日预算上限；③ `is_enabled`
  从"死功能"变为真实生效——检索前过滤禁用文档集合，PATCH 关闭的文档立即从 dense/sparse
  结果消失（含管理员全局检索），切换可逆无需重新索引。6 个回归测试。
- **异常信息脱敏（G10.6）**：orchestrator 崩溃/Agent 链路未预期异常不再把 `str(e)`（路径、
  连接串、供应商错误原文等内部细节）透传给客户端——SSE `error` 事件与非流式响应统一收敛为
  稳定错误码 `ORCHESTRATOR_ERROR` + 通用文案，真实异常由服务端 `logger.exception` 记录。
  2 个回归测试（流式/非流式均不含异常原文）。
- **认证安全收口（G10.5）**：① 注册按 IP 滑动窗口限流（`REGISTER_MAX_PER_WINDOW`，防批量
  注册），配合 `ALLOW_REGISTRATION` 总开关（生产可关闭开放注册）；② admin bootstrap 显式化——
  配置 `ADMIN_BOOTSTRAP_USERNAME` 后仅该用户名的首个注册者获得 admin，杜绝开放注册下攻击者
  "先注册先夺权"；③ `ensure_secrets()` 从"仅 production 强制"收紧为"任何非 local 环境
  （staging/test 等）缺失 `TOKEN_SECRET` / `DASHSCOPE_API_KEY` 一律拒绝启动"；④ 客户端 IP
  解析支持 `X-Forwarded-For`（取最左侧真实客户端），反代后登录/注册限流与审计的 IP 维度不再
  退化为 nginx 地址。6 个回归测试。
- **跨用户数据隔离（G10.1）**：`document_analysis` 检索强制带 `user_id`（`document_id`
  非全局唯一键，此前只按它过滤可跨用户读取他人文档）；orchestrator 禁止客户端
  `context` 覆盖身份字段（`user_id` / `student_id` / `session_id`）；重复上传路径校验
  属主——他人已上传的同内容文件返回 409 且不泄露其元数据。4 个回归测试。
- **CI 供应链防线（G9.5）**：新增 `security` CI job，双门禁——`gitleaks` 全 git 历史
  密钥泄漏扫描（含已删除文件）+ `pip-audit` 生产依赖已知漏洞审计（OSV 源）。
  任一检出即阻断 PR，从源头拦截 secrets 入库与带漏洞依赖合入。
- **生产依赖已知漏洞清零**：OSV 复核锁文件后整体升级 8 个含已知 CVE 的版本——
  `python-multipart` 0.0.18→0.0.32、`PyJWT` 2.10.1→2.13.0、`python-dotenv`
  1.1.0→1.2.2，langchain 生态 `core` 1.2.16→1.5.3 / `graph` 1.0.9→1.2.10 /
  `openai` 1.1.10→1.4.1 / `text-splitters` 1.1.1→1.1.2，以及 `fastapi`
  0.117.1→0.141.1（配套 `starlette` 0.48→1.4.1 安全锁 pin——0.48 有 20 条已知
  CVE，且不 pin 就不会被增量安装升级）；同时把 `pytest` / `pytest-asyncio` /
  `pytest-cov` 从生产锁迁入 `requirements-dev.txt`（本就是开发依赖，避免 dev 工具
  漏洞进入生产审计面）。升级后生产锁 21 包 0 已知漏洞，全量 142 测试在 langchain
  1.2→1.5、fastapi 0.117→0.141 升级后保持绿色。

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
