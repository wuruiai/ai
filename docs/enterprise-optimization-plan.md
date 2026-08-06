# 企业级优化方案(全量差距 → 具体改法)

> 目标:把"能跑的 demo"升级成**面试/简历拿得出手、可运维、可扩展**的企业级工程。
> 每条差距给出:**现状 → 改法 → 涉及文件 → 验证方式**。按 Phase 优先级推进。
>
> 依据:2026-08 代码审计 + 三路探索(后端基础设施 / API+前端 / 测试+CI+脚本)。

---

## 差距总览(面试价值 × 改动量)

| # | 差距 | 级别 | 面试价值 | 改动量 |
|---|------|------|---------|--------|
| G0.1 | 根目录 `main.py` 是 PyCharm 模板残留 | A | ★★★★★ | 极小 |
| G0.2 | 整个项目不是 git 仓库,无任何提交历史 | A | ★★★★★ | 小 |
| G0.3 | CI 的 ruff lint 被 `\|\| true` 吞掉(欺骗性绿) | A | ★★★★★ | 极小 |
| G0.4 | README 引用不存在的文档(断链) | A | ★★★ | 小 |
| G1.1 | 登录/注册无防爆破(无限流、无锁定) | B | ★★★★ | 中 |
| G1.2 | 手写 HMAC 伪 JWT;无 refresh / logout / 撤销 | B | ★★★★ | 大 |
| G1.3 | `TOKEN_SECRET` 可为空(重启 token 全失效);密钥校验缺失 | B | ★★★ | 小 |
| G1.4 | 改密/管理员降权后旧 token 仍有效最长 24h | B | ★★★★ | 中 |
| G1.5 | 无密码找回流程 | B | ★★ | 大(可后置) |
| G2.1 | 日志是纯文本;`logger.py` 声明的 JSON 结构化没实现;重复挂 handler | B | ★★★★ | 中 |
| G2.2 | 无 metrics / 无 /metrics 端点 | B | ★★★ | 中 |
| G2.3 | `/health` 未拆 live/ready;version 硬编码 | B | ★★★ | 小 |
| G2.4 | `main.py` 启动日志用 `print` 而非 logger | B | ★★ | 极小 |
| G3.1 | LLM token/成本不计账(budget 只数调用次数,内存态,多节点图算 1 次) | B | ★★★★★ | 中 |
| G3.2 | 引用验证是空壳(`citation.py` 全部返回 verified=True) | B | ★★★★★ | 中 |
| G3.3 | `confidence_router` HIGH 阈值不可达(fused score 上限 0.7) | C | ★★★ | 小 |
| G3.4 | 无 RAG 评测集,检索质量无法量化 | C | ★★★★ | 中 |
| G4.1 | 摄取是进程内 `asyncio.create_task`,重启丢任务、不可重试 | B | ★★★★ | 大 |
| G4.2 | 每次请求新建 SQLite 连接,无池;`connection_scope` 是死代码 | C | ★★★ | 中 |
| G4.3 | Chroma 同步调用阻塞事件循环 | C | ★★★ | 小 |
| G4.4 | 迁移框架无降级路径、无迁移日志、无审计 | C | ★★ | 中 |
| G5.1 | Docker 无 HEALTHCHECK、无 prod override、镜像无版本 | C | ★★★ | 中 |
| G5.2 | 备份是手动脚本,无定时、无保留策略、无校验 | C | ★★★ | 中 |
| G5.3 | 限流/预算内存态,单进程不共享(注释自认要换 Redis) | C | ★★ | 中 |
| G6.1 | 前端零测试(无 Vitest,CI 只 build) | B | ★★★★ | 中 |
| G6.2 | 前端死代码 `utils/sse.ts`;token 读取双通道(localStorage 直读) | C | ★★ | 小 |
| G6.3 | 前端无全局错误边界/加载骨架 | C | ★★ | 小 |
| G7.1 | 后端死代码:`memory.py`/`retry.py`/`create_embedding`/`citation_checker` | C | ★★ | 小 |
| G7.2 | 硬编码 DashScope base URL 未进 settings;health version 硬编码 | C | ★★ | 小 |

---

## Phase 0 — 仓库卫生(半天,面试救命题)

### G0.1 删除根目录 `main.py`
- **现状**:`main.py` 是 PyCharm 新建项目模板(`print_hi('PyCharm')`),与 `backend/main.py` 混淆,reviewer 一眼看到。
- **改法**:直接删除。确认无 import 引用(`grep -r "import main"` 无命中即可)。
- **验证**:`git rm main.py`(git 建好后),项目照常启动。

### G0.2 初始化 git + 干净首次提交
- **现状**:`git rev-parse` fatal,不是仓库。有 `.github/`、`.gitignore` 但没有历史。
- **改法**:
  1. `git init`
  2. 确认 `.gitignore` 已覆盖:`.env`、`data/`、`chroma/`、`.venv/`、`frontend/dist`、`node_modules`、`.pytest_cache/`、`.coverage`(已覆盖 ✓)
  3. `git add -A && git commit -m "feat: initial water-rag-local (RAG + 3-Agent 知识问答)"`
  4. 建议默认分支 `main`;后续按功能分支开发(`feat/auth`, `feat/observability`),合并用 `--no-ff`,保留 merge 记录(面试讲分支策略有东西讲)。
- **验证**:`git log --oneline` 有提交;`.env` 未进仓库(`git ls-files | grep .env` 为空)。

### G0.3 修复 CI lint 闸门
- **现状**:`.github/workflows/ci.yml:33` `ruff check backend scripts tests || true`,lint 失败照样绿。
- **改法**:
  ```yaml
  - name: Ruff lint
    run: ruff check backend scripts tests
  - name: Ruff format check
    run: ruff format --check backend scripts tests
  ```
  去掉 `|| true`。若现有代码有 lint 违规,先本地跑 `ruff check` 清到 0 再提交。
- **验证**:本地 `ruff check .` 通过;CI push 后 lint job 真实生效。

### G0.4 修复 README 断链 + 补架构图/API 表
- **现状**:`README.md:87-88` 链接的 `水利行业RAG+Agent_本地版部署清单_优化版.md`、`敲代码前期准备_修订版.md` 不存在(docs/ 只有 optimization-plan.md 和本文件)。
- **改法**:
  1. 删掉失效链接,换成真实存在的 `docs/optimization-plan.md`、`docs/enterprise-optimization-plan.md`。
  2. 补一段架构图(mermaid sequence):`前端 → /api/v1 → 路由 → orchestrator → Agent graph → RAG(retrieve → rerank) → LLM → SSE token 流 → 前端渲染`。
  3. 补 API 概览表(路由/方法/鉴权/说明,参考 `backend/api/router.py` 与本文 G 部分)。
- **验证**:README 里每个链接可点开;`npm run build` 与后端启动说明与实际一致。

---

## Phase 1 — 安全加固(Auth 体系)

> **实施状态(2026-08-06)**:G1.1 ✅ / G1.2 ✅ / G1.3 ✅ / G1.4 ✅ / G1.5 ⏸️(后置,见下)
>
> 实现要点(与下面"改法"略有出入,以实际代码为准):
> - G1.1: `LoginThrottle` 落地在 `backend/core/rate_limit.py`(滑动窗口 + 锁定期),`login()` 内按 `ip:<host>` / `user:<username>` 双键 `check()` + `record_failure()`;内存态(进程级),无 DB 表。
> - G1.2: 标准 PyJWT HS256;`refresh_tokens` 表存 jti + 过期 + revoked_at;`/auth/refresh` 轮换(旧 refresh 即吊销 + 重放检测);`/auth/logout`(单条或 all);`/auth/me` 实时返回 DB 权威角色。前端 axios 拦截器 401 时自动 refresh 重放一次,store 内单飞去重。
> - G1.3: `Settings.ensure_secrets()`(仅 `APP_ENV=production` 强制 TOKEN_SECRET / DASHSCOPE_API_KEY,缺失拒绝启动);`main.py` lifespan 首行调用。
> - G1.4: `users.token_version`;`get_current_user` async 校验 `ver == token_version`;改密/管理员改角色或停用均 bump + revoke refresh。

### G1.1 登录/注册防爆破
- **现状**:`backend/api/v1/auth.py` 的 login/register 无限流(`check_rate_limit` 只挂在 chat/upload)。暴力破解敞口,开放注册。
- **改法**:
  1. 新增 `backend/core/brute_force.py`:`LoginGuard`,按 `ip:username` 与 `ip` 双键滑动窗口(复用 `rate_limit.py` 的 `deque` 思路);失败 5 次锁定 15 分钟。
  2. 失败次数**落库**(新表 `login_attempts` 或复用 `audit_log` action=`auth.login_failed`,按时间窗查数)——比内存态更稳(重启不丢锁定态)。
  3. `login()`/`register()` 挂 `Depends(LoginGuard)`;失败时 `write_audit("auth.login_failed")`。
  4. `register` 可加配置开关 `ALLOW_REGISTRATION`(默认 true,生产可关)。
- **涉及文件**:`backend/api/v1/auth.py`、`backend/core/brute_force.py`(新)、`backend/db/migrations.py`(加 `login_attempts` 表,SCHEMA_VERSION→3)、`.env.example`。
- **验证**:`test_auth.py` 加"连续 N 次错误密码 → 429/锁定";重启服务锁定态仍在(DB 落库)。

### G1.2 升级标准 JWT + refresh token + 登出撤销
- **现状**:手写 HMAC 伪 JWT(`auth.py:82-96`),无 refresh、无 logout、无撤销;jti 生成了但从不校验。
- **改法**:
  1. 引入 `PyJWT`(加进 `requirements.lock.txt`),`generate_token`/`verify_token` 改为标准 HS256 实现,payload 保留 `{sub, username, role, token_version, jti, iat, exp, type}`(type=access|refresh)。
  2. 新增 `refresh_tokens` 表(DB 落库,带 `revoked` 标志、过期时间)。`POST /auth/refresh` 用 refresh token 换新 access + 轮换 refresh。
  3. `POST /auth/logout`:标记该 refresh token revoked;access token 走 `token_blacklist`(jti + 过期时间)实现立即失效。`get_current_user` 校验时查黑名单。
  4. 前端 `logout()` 调 `/auth/logout` 后再清 localStorage。
- **涉及文件**:`backend/api/v1/auth.py`、`backend/db/migrations.py`(v3 加 `refresh_tokens` + `token_blacklist`)、`backend/core/security.py`、`frontend/src/stores/auth.ts`、`frontend/src/api/auth.ts`、`.env.example`、`requirements.lock.txt`。
- **验证**:`test_auth.py` 覆盖 refresh 轮换、logout 后 access 立即 401、refresh 复用被拒。

### G1.3 密钥强制 + 生产校验
- **现状**:`TOKEN_SECRET` 默认 `""` → 进程随机,重启全部 token 失效。
- **改法**:
  1. `Settings` 增加 validator:`APP_ENV != "local"` 时 `TOKEN_SECRET` 为空则启动报错(`raise ValueError`)。
  2. `.env.example` 注释强调生产必填。
- **涉及文件**:`backend/config.py`、`.env.example`。
- **验证**:`APP_ENV=prod` 且无 `TOKEN_SECRET` 启动 → 拒绝启动。

### G1.4 改密/降权立即失效旧 token
- **现状**:role 在 token payload 里,admin 改角色后旧 token 最长 24h 仍有效。
- **改法**:
  1. `users` 表加 `token_version INT DEFAULT 0`。
  2. token 带 `token_version` claim;`get_current_user` 校验时与用户当前 `token_version` 比对,不一致 → 401。
  3. `change_password`、`admin` 改角色/禁用时 `token_version += 1`(同时 revoke refresh token)→ 该用户所有 token 立即失效。
  4. 为减少每请求 DB 读,`get_current_user` 改 async,查 `users.token_version`;可加 TTL 缓存(admin 变更时主动失效)。
- **涉及文件**:`backend/api/v1/auth.py`(get_current_user 改 async)、`backend/api/v1/admin.py`、`backend/db/migrations.py`、`backend/core/security.py`、所有依赖方(自动生效,无需改调用)。
- **验证**:`test_auth.py` 改密后旧 token 401;`test_admin_api.py` 降权后旧 token 401。

### G1.5 密码找回(可后置)
- **改法**:`POST /auth/forgot-password`(发重置链接/验证码)+ `POST /auth/reset-password`(带一次性 token)。因需邮件/短信通道,建议 Phase 1 后置或交给管理员后台重置(admin 已有改用户端点,补"管理员重置密码"即可)。
- **验证**:重置后旧密码失效、新密码可登录。

---

## Phase 2 — 可观测性

> **实施状态(2026-08-06)**:G2.1 ✅ / G2.2 ✅ / G2.3 ✅ / G2.4 ✅
>
> 实现要点(与下面"改法"的出入以实际代码为准):
> - G2.1: `logger.py` 重构为**根 logger 单次配置**(幂等 `setup_logging`),`JsonFormatter` 输出单行 JSON
>   (UTC 毫秒时间戳 + level + logger + message + request_id + exc_info),`request_id` 走 `ContextVar`。
>   文件日志为按天命名 JSON 文件(未用 TimedRotatingFileHandler,保留策略简单起见按天自然轮换)。
> - G2.2: `prometheus-client==0.26.0`;`backend/core/metrics.py` 提供 `http_requests_total` +
>   `http_request_duration_seconds`;`main.py` 中间件打点;**路径归一化**(UUID/长 hex/长数字 → `{id}`)
>   防标签基数爆炸;**/metrics 自身不计数**。`GET /metrics` 免令牌(标准抓取方式,可后续加 bearer)。
> - G2.3: `/health` 纯存活探针(零依赖,供 docker HEALTHCHECK);`/health/ready` 查 SQLite+Chroma
>   全可达才 200 否则 503。version 仍硬编码(未接 importlib.metadata,见 G7.2 收口)。
> - G2.4: `main.py` / `migrations.py` 全部 `print` → `logger`(`logger.exception` 带堆栈)。

### G2.1 结构化 JSON 日志 + request_id 贯穿
- **现状**:`backend/core/logger.py` docstring 声称 JSON 结构化,实际是纯文本;每次 `get_logger()` 重复挂 handler;`request_id` 中间件生成了但从未进日志;`main.py` 用 `print`。
- **改法**:
  1. 重构 `logger.py`:
     - 每个 logger 名只挂一次 handler(用 `if not logger.handlers` 去重)。
     - 自定义 `JSONFormatter`:`{"ts", "level", "logger", "message", "request_id", "extra"}`。
     - 文件 handler 用 `logging.handlers.TimedRotatingFileHandler`(按天滚动 + `backupCount` 保留策略)。
     - `settings.LOG_LEVEL` 真正应用到所有 handler。
  2. `main.py` 的 request_id 中间件用 `contextvars.ContextVar` 写入;`logger.py` 的 JSONFormatter 自动读该 ContextVar 打进日志。
  3. `main.py` 启动/关闭日志 `print` → `logger.info()`。
- **涉及文件**:`backend/core/logger.py`、`backend/main.py`、`backend/config.py`(加 `LOG_RETENTION_DAYS`)。
- **验证**:跑一次请求,看 `data/logs/` 出现 JSON 行且带 `request_id`;两次 `get_logger(__name__)` 不重复打印。

### G2.2 /metrics(Prometheus)
- **现状**:无任何 metrics。
- **改法**:
  1. 引入 `prometheus-client`(标准、轻量,加进 requirements.lock.txt)。
  2. 新增 `backend/core/metrics.py`:定义 Counter/Histogram——`http_requests_total{method,status,path}`、`http_request_duration_seconds`、`llm_calls_total{model}`、`db_connections_total`。
  3. `main.py` 中间件对每个请求计数+计时;`/metrics` 路由暴露文本格式。
  4. 无鉴权(标准做法),或放 `/api/v1/metrics` + `require_admin`(本机部署建议后者避免泄露)。
- **涉及文件**:`backend/core/metrics.py`(新)、`backend/main.py`、`backend/api/router.py`、`requirements.lock.txt`。
- **验证**:`curl http://127.0.0.1:8001/metrics` 返回 Prometheus 文本;压几个请求后计数增长。

### G2.3 health 拆 live/ready + 版本从元数据读
- **现状**:`/health` 一个端点,`_check_deps()` 里同步 `vector_store.count()`(大集合慢);version 硬编码 `"1.0.0"`。
- **改法**:
  1. `GET /health/live`:仅返回进程存活(不查依赖)。
  2. `GET /health/ready`:查 SQLite `SELECT 1` + Chroma count;超时保护(如 2s)。
  3. version 从 `importlib.metadata.version("water-rag")` 或模块 `__version__` 读,不再硬编码。
- **涉及文件**:`backend/api/v1/health.py`、`backend/main.py`、`backend/config.py`。
- **验证**:curl 两个端点;DB 停掉后 `/ready` 返回 degraded、`/live` 仍 ok。

### G2.4 print → logger
- **改法**:`main.py:33/48/63/167` 等 print 统一 `get_logger(__name__)`。
- **验证**:启动日志进入文件且带时间戳/级别。

---

## Phase 3 — 成本与检索质量

> **实施状态(2026-08-06)**:G3.3 ✅ / G3.4 ✅ / G3.1 ✅ / G3.2 ✅ —— Phase 3 完成
>
> - G3.3: 置信度改"顶分 + 领先 margin"相对信号（min-max 归一化下绝对阈值不可比，
>   旧 HIGH=0.7 单一路径 fused 分最高仅 0.7 不可达）；`get_confidence_router` 改真单例；补三档单测。
> - G3.4: `docs/eval_set.jsonl`（8 条水利场景评测样本）+ `scripts/evaluate_rag.py`（纯检索评测：
>   recall@k / hit_rate@k / MRR@k → Markdown 报告，标题→真实 document_id 解析，缺文档按未命中计并 WARN）；
>   `tests/unit/test_eval_script.py` 覆盖指标计算/标题解析/报告渲染/评测集合法性。
> - G3.1: 迁移 v4 `llm_usage` 表 + `backend/core/usage.py` `UsageCollector`（on_llm_end 捕获
>   token usage，与 TokenStreamHandler 同链，请求结束 flush 落库）；价格配置
>   `LLM_PRICE_INPUT_PER_M/OUTPUT_PER_M`（元/百万 token）；`GET /admin/usage` 聚合累计 + 近 N 天趋势；
>   chat.py 流结束记账；`tests/unit/test_usage.py` 覆盖提取/落库/成本/管理端。
> - G3.2: `citation.py` 改真实现——答案×引用内容字符 2-gram 词汇覆盖判定 verified/coverage；
>   chat.py 答案生成后核实并推送 `citation_verdict` SSE 事件 + 落库带 verified；前端
>   CitationPanel 增"已核实/待核实"徽标，历史/实时均展示；`tests/unit/test_citation.py` 覆盖
>   覆盖/改述/无关/空答案/空内容。**说明**：`verify_citation` 需要 answer 已生成，故流式
>   引用在答案到达后回传 verdict 事件即时更新面板。

### G3.1 LLM token/成本记账
- **现状**:`TokenStreamHandler` 只实现 `on_llm_new_token`,丢弃 `usage_metadata`;`BudgetManager` 按"每次 orchestrator 完成"数 1 次调用(4 节点图算 1 次),内存态不持久。
- **改法**:
  1. `TokenStreamHandler` 补 `on_llm_end`(读 `response.usage_metadata` 的 prompt/completion tokens)。
  2. 新增 `backend/core/usage.py`:`record_llm_usage(user_id, request_id, model, input_tokens, output_tokens, duration_ms)`,写入新表 `llm_usage`(model/prompt_tokens/completion_tokens/estimated_cost/user_id/request_id/created_at)。
  3. `ModelFactory.create_llm` 附加一个统一 callback 链(`callbacks + [UsageCollector]`),覆盖所有 agent 节点;embedding/rerank 路径也补。
  4. 单次请求结束时(chat.py finally)flush 到 DB。
  5. 管理端:`GET /api/v1/admin/usage`(按日聚合 token/成本,`admin.py`)。
- **涉及文件**:`backend/core/token_stream.py`、`backend/core/usage.py`(新)、`backend/core/model_factory.py`、`backend/rag/embedding.py`、`backend/rag/reranker.py`、`backend/db/migrations.py`(v3 加 `llm_usage`)、`backend/api/v1/admin.py`、`backend/config.py`(加单 token 价格映射)。
- **验证**:一次真实对话后 `llm_usage` 有 N 条(节点数 N);`/admin/usage` 能聚合;单测 mock LLM 验证 token 数记录。

### G3.2 实现真实引用验证(防幻觉)
- **现状**:`backend/rag/citation.py` 的 `verify_citation` 是空壳,全部 `{"verified": True}`,且该模块从未被调用;引用在 `chat.py._fetch_top_evidence` 手搓。
- **改法**:
  1. 实现 `CitationChecker.verify_citation(answer, citations)`:对每条引用,用 LLM 判断"答案内容是否被该 chunk 支持",返回 `{verified, reason}`;可加词法兜底(答案关键词在 chunk 中的覆盖率)降本。
  2. `chat.py` 生成引用后接入验证,`verified=False` 的引用降级显示(前端 `CitationPanel` 加"待核实"样式)。
  3. 删除旧的"从未被调用"路径,统一走 `citation_checker`。
- **涉及文件**:`backend/rag/citation.py`、`backend/api/v1/chat.py`、`frontend/src/components/CitationPanel.vue`、`backend/core/model_factory.py`。
- **验证**:`test_citation.py` 新单测(支持/不支持两类样例);真实对话中问题文档外的引用被标记未核实。

### G3.3 修复 confidence_router 阈值不可达
- **现状**:`confidence_router.py` `HIGH_CONFIDENCE_THRESHOLD=0.7`,但 fused score 上限是 `DENSE_WEIGHT=0.7`(retriever 融合),HIGH 永不触发;`get_confidence_router()` 名为单例实为新实例。
- **改法**:重新校准——对 fused score 做二次归一化或按 retrieval source 分层评估;阈值下调到可达值;`get_confidence_router` 改真单例。补单测覆盖 HIGH/MEDIUM/LOW 三档。
- **涉及文件**:`backend/core/confidence_router.py`、`tests/unit/test_rag_pipeline.py`。
- **验证**:构造高相关检索结果能产出 HIGH 档。

### G3.4 RAG 评测集
- **现状**:无 eval 集,检索质量无法量化,简历上"混合检索效果好"缺数据支撑。
- **改法**:
  1. `docs/eval_set.jsonl`:`{"question", "expected_doc_ids": [...], "answer_fragment": "..."}`,5-10 条水利领域真实样例。
  2. `scripts/evaluate_rag.py`:对每条问题跑 `retriever.retrieve()`(不发 LLM,离线可跑),算 **recall@k / MRR / hit_rate**,输出 Markdown 报告。
  3. 在 `docs/` 固化一份基线报告。
- **涉及文件**:`scripts/evaluate_rag.py`(新)、`docs/eval_set.jsonl`(新)。
- **验证**:跑脚本出报告;调整 `DENSE_WEIGHT` 后数值变化可解释。

---

## Phase 4 — 任务与数据层

> **实施状态(2026-08-06)**:G4.2 ✅ / G4.3 ✅ / G4.4 ✅ / G4.1 ⏳（池与迁移强化先行，任务队列收尾）
>
> - G4.2: `backend/db/connection.py` 改有界连接池 `SQLitePool`（asyncio.Queue 空闲复用，
>   `get_connection`=checkout / `close_db`=checkin 签名不变，调用方零改动）；归还时 rollback
>   清遗留事务；新增 `close()`（lifespan 关闭时调用，否则 aiosqlite 后台线程拖住进程退出）；
>   配置 `DB_POOL_SIZE=5`（<=1 禁用池化，测试 conftest 强制 0 保证 `_fresh_db` 删库语义）；
>   `tests/unit/test_connection_pool.py` 7 用例覆盖复用/上限/事务清理/关闭。
> - G4.3: `vector_store.py` 公开方法改 async，内部 `asyncio.to_thread` 把同步 Chroma 调用
>   丢线程池（不再阻塞事件循环）；调用点 retriever/health/documents/ingestion_worker 全量
>   改 `await`；`test_vector_store.py` 改 async，`test_retriever.py` FakeVS 改 async。
> - G4.4: `migrations.py` 加 `migration_log` 审计表（migrate 每步写 applied、降级写 rolled_back，
>   与版本号同一事务）；补 `downgrade(db, to)` + `_downgrade_v2/v3/v4`（SQLite DROP COLUMN/TABLE，
>   均事务性）；新增 `scripts/downgrade_db.py --to`；`test_migrations.py` 7 用例覆盖日志、
>   降级到 v2/v1、降级再升级回补。全套 pytest 115 绿。

### G4.1 摄取任务持久化队列
- **现状**:`documents.py:47-51` `asyncio.create_task` 进程内任务,重启丢任务、无重试、多 worker 重复摄取。
- **改法**:
  1. 复用已有 `ingestion_tasks` 表作**持久化队列**,新增 claim 语义:worker `UPDATE ingestion_tasks SET status='running', claimed_at=? WHERE task_id=? AND status='pending'` 原子抢占,带 lease(如 10 分钟超时)。
  2. 新增 `backend/tasks/queue.py`:`enqueue(document_id)` + `claim()` + `worker_loop()`。
  3. 新增 `scripts/worker.py`:`python -m scripts.worker` 独立进程跑 worker_loop(生产多进程可多开)。本机默认仍在应用进程内跑一个 asyncio worker(行为不变,但走持久化队列)。
  4. 启动时把残留 `running` 超 lease 的任务回滚为 `pending`(可重试)或 `failed`(超次数)。
  5. 失败任务 `attempts` 超限(如 3)标 `failed`。
- **涉及文件**:`backend/tasks/queue.py`(新)、`backend/tasks/ingestion_worker.py`、`backend/api/v1/documents.py`、`backend/db/migrations.py`(v3 给 `ingestion_tasks` 加 `claimed_at/attempts` 列)、`scripts/worker.py`(新)。
- **验证**:杀掉 worker 中途重启,`pending` 任务被重新拉起;`test_rag_pipeline.py` 补"中断后恢复"用例;双进程跑不重复 ingest(claim 幂等)。

### G4.2 SQLite 连接池(不改公共 API)
- **现状**:`get_connection()` 每次新建连接(~30 处手动 open/close);`connection_scope()` 死代码。
- **改法**(关键设计:**保持 `get_connection`/`close_db` 签名不变,内部改为池**,调用方零改动):
  1. `backend/db/connection.py` 加 `SQLitePool`:`asyncio.Queue` 存 N 个预建连接(默认 `DB_POOL_SIZE=5`),`get_connection()` = checkout,`close_db()` = checkin;空池时新建(上限保护)。
  2. PRAGMA 只在建池时设一次。
  3. `connection_scope()` 改为基于池的上下文管理器(并真正开始使用,替换一批 try/finally)。
- **涉及文件**:`backend/db/connection.py`、`backend/config.py`(加 `DB_POOL_SIZE`)、`backend/api/v1/auth.py`(顺带改 async 用 scope)。
- **验证**:并发 20 请求下 `PRAGMA schema_version` 数到的连接数 ≤ 池上限;现有 60+ 单测全绿。

### G4.3 Chroma 同步调用移出事件循环
- **现状**:`vector_store.query/count/delete` 全同步,阻塞 async 事件循环。
- **改法**:调用点包 `await asyncio.to_thread(vector_store.query, ...)`,集中在 `retriever.py`、`ingestion_worker.py`、`documents.py`、`health.py` 四处。
- **涉及文件**:`backend/rag/retriever.py`、`backend/rag/vector_store.py`(内部查询方法不阻塞的文档注释)、`backend/api/v1/documents.py`、`backend/api/v1/health.py`。
- **验证**:上传大文档时 SSE 对话不被卡住(压测观察事件循环延迟)。

### G4.4 迁移框架强化
- **现状**:手写版本化迁移(SCHEMA_VERSION=2),幂等 + 前向守卫;无降级、无迁移日志、无审计。
- **改法**:
  1. 加 `migration_log` 表(记录每次应用的 migration 名、时间)。
  2. 每步迁移包在显式事务 + 成功后写日志。
  3. 补 `_downgrade_vN`(SQLite 能力范围内,删表/回滚列),`scripts/downgrade_db.py`。
  4. `test_migrations.py` 补迁移日志断言。
  - **说明**:不强行引入 Alembic(SQLite + FTS5 触发器 + ALTER 约束下迁移手写反而清晰);留"迁移到 Postgres 时换 Alembic"的演进说明。
- **涉及文件**:`backend/db/migrations.py`、`scripts/downgrade_db.py`(新)、`tests/unit/test_migrations.py`。
- **验证**:`migrate()` 跑完 `migration_log` 有记录;降级脚本 v2→v1 不报错。

---

## Phase 5 — 部署与运维

### G5.1 Docker HEALTHCHECK + prod override
- **现状**:`docker-compose.yml` 无 HEALTHCHECK;backend 无;镜像无版本标签;frontend 硬绑宿主 80。
- **改法**:
  1. `Dockerfile` 加 `HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health/ready', timeout=3)"`。
  2. `docker-compose.yml` 补 `healthcheck`(interval/start_period),frontend 端口改 `${FRONTEND_PORT:-80}:80`。
  3. 新增 `docker-compose.prod.yml`:env 注入、镜像 tag `${VERSION:-latest}`、`restart: always`。
  4. 文档化 build+push:`docker build -t ghcr.io/<user>/water-rag-backend:${VERSION} .`
- **涉及文件**:`Dockerfile`、`docker-compose.yml`、`docker-compose.prod.yml`(新)、`README.md`。
- **验证**:`docker compose up` 后 `docker ps` 显示 healthy;backend 挂掉自动重启。

> **✅ 已实测修复(2026-08-06)**:Docker 后端起不来的根因是 **frontend 镜像构建卡死**——
> 后端镜像(886MB)构建成功、运行正常,但 **frontend 镜像从没构建成功**(`docker images` 里只有 backend)。
> 根因:宿主 npm 走 `registry.npmmirror.com`(`~/.npmrc`),但项目里没有 `frontend/.npmrc`,
> `frontend/Dockerfile` 的 `RUN npm ci` 在容器内走**官方 npm 源**,国内网络下卡死/超时。
> 已修复:
> 1. 新建 `frontend/.npmrc`(registry=npmmirror)+ `frontend/Dockerfile` 在 `npm ci` 前 `COPY .npmrc ./`。
> 2. `Dockerfile` 加 `HEALTHCHECK` + `ENV ANONYMIZED_TELEMETRY=False`。
> 3. `docker-compose.yml`:frontend `depends_on: backend: condition: service_healthy`,端口 `${FRONTEND_PORT:-80}`。
> 4. **Chromadb 遥测噪声**:`ANONYMIZED_TELEMETRY=False` 和 `Settings(anonymized_telemetry=False)` 都压不住——
>    根因是 chromadb 0.6.3 调 `posthog.capture(user_id, event, props)` 传 3 个位置参数,而 posthog 的
>    `capture(event, **kwargs)` 只收 1 个位置参数,在 `disabled` 标志判断**之前**就抛 TypeError。
>    已加 `backend/rag/vector_store.py` 的 `silence_chroma_telemetry()`(幂等 monkeypatch `posthog.capture` → no-op)。
> 验证:`docker compose up -d` 后端 `(healthy)`、前端 200、`/health` ok、启动日志零遥测噪声、60 个单测全绿。

### G5.2 备份自动化 + 保留策略 + 校验
- **现状**:`scripts/backup_data.py` 纯手动,无定时、无保留、无校验。
- **改法**:
  1. `config.py` 加 `BACKUP_ENABLED`、`BACKUP_RETENTION_DAYS`(默认 7)。
  2. `scripts/backup_data.py` 加 `--retention-days` 参数,备份后裁剪过期目录。
  3. 备份后执行校验:`sqlite3` `PRAGMA integrity_check` + 确认 Chroma 目录非空。
  4. 接入 worker 循环(`scripts/worker.py` 每日一次)或新增 `scripts/backup_cron.py`(Windows 计划任务/docker cron 说明)。
- **涉及文件**:`scripts/backup_data.py`、`backend/config.py`、`scripts/worker.py`、`.env.example`、`README.md`。
- **验证**:跑两次备份,旧目录按天数被清理;备份目录内 `integrity_check` 输出 ok。

### G5.3 限流/预算抽象成可换 Redis
- **现状**:`rate_limit.py`、`budget.py` 内存态,单进程。
- **改法**:
  1. 定义协议:`RateLimitBackend` / `BudgetBackend`(check + record)。
  2. 保留 `InMemoryBackend`;新增 `RedisBackend`(可选依赖 `redis`,settings `REDIS_URL` 非空才启用)。
  3. 实例化走工厂:`get_rate_limit_backend()`。
  - 不强制引入 redis 依赖,默认内存实现;文档写明"多 worker 时设 REDIS_URL 即切换"。
- **涉及文件**:`backend/core/rate_limit.py`、`backend/core/budget.py`、`backend/config.py`、`.env.example`。
- **验证**:单测用假 Redis 或内存实现全绿;设置 `REDIS_URL` 后行为不变。

---

## Phase 6 — 前端工程化

### G6.1 Vitest 单元/组件测试 + CI 接入
- **现状**:`frontend/package.json` 无 test 脚本,无 vitest/@vue/test-utils,CI 只 build。
- **改法**:
  1. 加 devDeps:`vitest`、`@vue/test-utils`、`jsdom`、`@vitest/coverage-v8`。
  2. `package.json` scripts 加 `"test": "vitest run"`、`"test:watch": "vitest"`;`vite.config.ts` 加 `test` 块(environment=jsdom)。
  3. 首批用例:
     - `stores/auth.ts` — setAuth/clearToken/localStorage 持久化
     - `api/chat.ts` 的 SSE 解析器 — token/citation/done 事件解析(纯函数,把解析逻辑提纯便于测)
     - `stores/chat.ts` — sendMessage 流式拼接/错误分支(mock `streamChat`)
     - 组件冒烟:`LoginView` 表单提交、`CitationPanel` 渲染
  4. CI frontend job 加 `npm run test -- --run`。
- **涉及文件**:`frontend/package.json`、`frontend/vite.config.ts`、`frontend/src/**/*.test.ts`(新)、`.github/workflows/ci.yml`。
- **验证**:`npm run test` 全绿;CI push 后前端 job 先 type-check 再 test 再 build。

### G6.2 前端清理 + token 单通道
- **现状**:`utils/sse.ts` 死代码;SSE 用 `localStorage.getItem('token')` 直读,store 又是另一处,双通道易漂移。
- **改法**:
  1. 删除 `frontend/src/utils/sse.ts`(active 解析在 `api/chat.ts`)。
  2. 抽 `frontend/src/utils/token.ts`:`getToken()` 统一从 auth store 读(SSE 与 axios 同源)。
  3. router 守卫改走 store,不直接读 localStorage。
- **涉及文件**:`frontend/src/utils/sse.ts`(删)、`frontend/src/utils/token.ts`(新)、`frontend/src/api/chat.ts`、`frontend/src/router/index.ts`。
- **验证**:`vue-tsc --noEmit` 通过;grep 确认 localStorage 只出现在 auth store / token util。

### G6.3 全局错误边界 + 加载态
- **改法**:
  1. `App.vue` 加全局 `ErrorBoundary` 组件(渲染失败显示降级卡片,不白屏)。
  2. 路由级 loading(已有 axios/SSE 局部 loading,补 `router` 懒加载 Suspense 或骨架)。
  3. 统一 `extractError` 已存在,补到 ChatView 的发送失败路径(已部分有)。
- **涉及文件**:`frontend/src/App.vue`、`frontend/src/components/ErrorBoundary.vue`(新)、`frontend/src/views/ChatView.vue`。
- **验证**:手动触发组件抛错 → 显示降级 UI 而非白屏。

---

## Phase 7 — 清理与配置补全

### G7.1 死代码清理
- **现状**(探索确认):`backend/core/memory.py`(全文件无人 import)、`backend/core/retry.py`(`create_retry_decorator` 无人用)、`model_factory.create_embedding`(embedding.py 绕过 LangChain)、`rag/citation.py`(G3.2 落地后复用)、`connection_scope`(G4.2 落地后复用)。
- **改法**:`memory.py`、`retry.py` 直接删除(retry 逻辑可并入 `http_client.py` 的 httpx 重试);其余随对应 Phase 复活。
- **验证**:`python -c "import backend"` 无报错;grep 无悬挂 import。

### G7.2 配置/硬编码收口
- **改法**:
  1. `model_factory.py`、`embedding.py` 的 DashScope base URL 抽到 `settings`(如 `DASHSCOPE_BASE_URL`)。
  2. `health.py` version 读包元数据(G2.3 已含)。
  3. `.env.example` 补全本次所有新配置项。
- **涉及文件**:`backend/config.py`、`backend/core/model_factory.py`、`backend/rag/embedding.py`、`.env.example`。

---

## 依赖变更汇总(requirements.lock.txt)

| 新增包 | 用途 | Phase |
|--------|------|-------|
| `PyJWT` | 标准 JWT 签发/校验 | 1 |
| `prometheus-client` | /metrics 指标 | 2 |
| `redis`(可选) | 多进程限流/预算后端 | 5 |
| 前端 devDeps:`vitest`/`@vue/test-utils`/`jsdom` | 前端测试 | 6 |

---

## 执行顺序建议

1. **Phase 0 当天做掉**(仓库卫生,面试救命)。
2. **Phase 1 安全**(Auth)是第二大面试点,独立提交、独立测试。
3. **Phase 2 可观测 + Phase 3 成本/引用验证**:这两块是"比 demo 高一级"的差异化亮点,优先于纯工程化。
4. **Phase 4-6** 按时间排;**Phase 7 清理**见缝插针。

每 Phase 完成即跑:`pytest --cov=backend`、`ruff check .`、`npm run test`、`npm run build`,保持 CI 全绿。
