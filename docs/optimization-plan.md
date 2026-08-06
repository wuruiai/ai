# 企业级优化方案(代码级)

> 目标:把"能跑的 demo"升级成"面试/简历拿得出手、可运维、可扩展的企业级工程"。
> 每条给出:现状 → 目标 → 具体代码改动 → 验收标准。按 P0 优先级排序推进。

---

## P0-1 测试体系 + CI(最优先)

**现状**:只有 `scripts/smoke_test.py` 一个集成冒烟;无单元测试、无覆盖率、无 CI。
**目标**:pytest 单测 + 覆盖率 + GitHub Actions 自动跑。

**代码改动**:
1. 新建 `requirements-dev.txt`:
   ```
   pytest>=8
   pytest-asyncio
   pytest-cov
   ruff
   ```
2. 单测目录 `tests/unit/`(全部 mock 掉 DashScope,不发真实请求):
   - `test_auth.py`:注册/登录/改密/错误密码/token 签名校验(用 `TestClient` + 临时 DB)
   - `test_migrations.py`:v1→v2 幂等(连跑两次 `migrate()` 第二次为空)、schema_version 拒绝降级
   - `test_retriever.py`:`_normalize`(min-max/单值 0.5)、dense+sparse 融合权重、user_id 过滤
   - `test_ids.py`:`generate_chunk_id` 唯一性(DOCX 多段落场景)
   - `test_chunker.py`:`chunk_pages` page/chunk_index 契约
   - `test_rag_pipeline.py`(mock embedding):`ingest_document` 状态机 + 失败回滚
   - `test_budget.py`:`check_budget` 超限抛 429
   - `test_security.py`:`validate_origin`、`validate_file_path` 路径穿越
   - `test_token_stream.py`:`TokenStreamHandler` 入队
3. CI `.github/workflows/ci.yml`:
   ```yaml
   jobs:
     backend:
       steps: setup-python 3.11 → pip install -r requirements-dev.txt
              → pytest --cov=backend --cov-fail-under=70
              → python -m scripts.smoke_test   # 已隔离临时 DB
     frontend:
       steps: setup-node 20 → npm ci → npm run build → npm run lint
   ```
4. `pytest.ini`:`asyncio_mode = auto`、`testpaths = tests/unit`

**验收**:`pytest --cov` 全绿且覆盖率 ≥70%;push 后 CI 两 job 自动跑。

---

## P0-2 容器化部署

**现状**:无 Docker,部署靠手敲命令。
**目标**:`docker compose up` 一键起全栈。

**代码改动**:
1. `Dockerfile`(后端,多阶段):
   ```dockerfile
   FROM python:3.11-slim AS base
   WORKDIR /app
   COPY requirements.lock.txt .
   RUN pip install --no-cache-dir -r requirements.lock.txt
   COPY backend/ backend/ ; COPY scripts/ scripts/ ; COPY main.py .
   EXPOSE 8001
   CMD ["python","-m","uvicorn","backend.main:app","--host","0.0.0.0","--port","8001"]
   ```
2. `Dockerfile.frontend`(多阶段:node build → nginx 伺服 dist + 反代 /api→backend):
   - `frontend/nginx.conf`:`location /api/ { proxy_pass http://backend:8001; }`,`/health` 同反代,其余 serve dist
3. `docker-compose.yml`:
   ```yaml
   services:
     backend: { build: ., ports: ["8001:8001"], env_file: .env, volumes: [./data:/app/data] }
     frontend: { build: ./frontend, ports: ["80:80"], depends_on: [backend] }
   ```
4. `.dockerignore`(`.venv/ node_modules/ data/ chroma/ dist/`)
5. 生产入口在启动前跑迁移(`scripts.init_db`),与 lifespan 幂等迁移一致

**验收**:`docker compose up --build` 后浏览器 80 端口可登录、上传、问答。

---

## P0-3 可观测性:结构化日志 + /metrics

**现状**:print/logger 无结构;无指标端点。
**目标**:请求日志含耗时/状态/request-id;Prometheus `/metrics`。

**代码改动**:
1. `backend/core/logging.py` 增强:JSON Formatter,字段 `ts/level/logger/msg/request_id/duration_ms/status/method/path`
2. `backend/main.py` 请求中间件升级(替换现有 `add_request_id`):
   ```python
   @app.middleware("http")
   async def access_log(request, call_next):
       t0 = time.perf_counter()
       resp = await call_next(request)
       logger.info("http", extra={"method":..., "path":..., "status":..., "duration_ms":..., "request_id":...})
       return resp
   ```
3. `backend/api/v1/metrics.py`(用 `prometheus_client` 或手写 text 格式):
   - Counter `http_requests_total{method,status}`
   - Histogram `http_request_duration_seconds`
   - Counter `llm_tokens_total`(在 chat.py record_call 处累加)
   - Counter/Gauge `ingestion_documents_total{status}`(ingestion_worker 状态机处)
   - `GET /metrics`(绑定 127.0.0.1 或 require_admin)
4. 可选 `sentry_sdk`:`config` 加 `SENTRY_DSN: str=""`,`main.py` 非空时 init

**验收**:每条请求有含 duration/request_id 的结构化日志;`curl /metrics` 返回 Prometheus 格式;设 `SENTRY_DSN` 后异常上报。

---

## P0-4 API 规范:分页 + 统一错误 + 每用户限流

**现状**:列表无分页;HTTPException detail 格式不一;无每用户限流。
**目标**:分页返回、统一错误 envelope、per-user 限流。

**代码改动**:
1. **分页**:
   - `documents.py list_documents`、`threads.py list_threads`、`admin.py admin_audit` 加 `limit/offset` 参数 + `total`(已有 total) + `page`/`page_size` 风格;SQL 加 `LIMIT ? OFFSET ?`
   - 响应加 `page/page_size/total` 元数据
   - 前端 KnowledgeView/AdminView 加"加载更多/分页"
2. **统一错误** `backend/core/exceptions.py`:
   ```python
   @app.exception_handler(HTTPException)
   async def _(req, exc):
       return JSONResponse(status_code=exc.status_code, content={
           "error": {"code": f"http_{exc.status_code}", "message": exc.detail, "request_id": req.headers.get("X-Request-ID")}
       })
   ```
   校验现有 `raise HTTPException(detail=...)` 的语义迁移到 `message`
3. **每用户限流** `backend/core/rate_limit.py`(内存滑动窗口):
   ```python
   class RateLimiter:  # {user_id: deque[timestamps]}
       def allow(self, user_id, limit=30, window_s=60) -> bool
   ```
   在 `get_current_user` 后加 `Depends`(chat/upload 等重端点),超限 429

**验收**:列表接口 `?page=1&page_size=20` 正常;错误响应统一 `{"error":{code,message,request_id}}`;连续请求触发 429。

---

## P0-5 后台任务队列(持久化)

**现状**:`documents._spawn_ingestion` 用 `asyncio.create_task`,进程重启即丢。
**目标**:SQLite 持久队列 + 单一 worker,重启恢复未完成任务。

**代码改动**:
1. 复用 `ingestion_tasks` 表作队列(已有):`status='pending'` 即待执行
2. `backend/tasks/ingestion_worker.py` 加一个常驻 worker:
   ```python
   async def worker_loop():
       while True:
           task = await _claim_next_task()   # UPDATE ingestion_tasks SET status='parsing' WHERE task_id IN (SELECT ... WHERE status='pending' LIMIT 1) RETURNING
           if task: await _run_task(task)
           else: await asyncio.sleep(1)
   ```
   用 `BEGIN IMMEDIATE` 抢占防并发重复
3. `_spawn_ingestion` 改为只插 `ingestion_tasks(status='pending')`,不直接 create_task;启动时(或 lifespan)起 worker
4. 崩溃恢复:worker 启动时把 `status IN ('parsing','chunking',...)` 的遗留任务重置为 `pending`(配合现有 documents 状态恢复)

**验收**:并发上传不丢任务;杀进程后重启,遗留任务自动重跑;`ingestion_tasks.status` 中间态也更新(顺带修掉之前遗留的"任务表进度不准")。

---

## P1-6 前端工程化

**现状**:无前端测试、无 i18n、无暗色模式、无 lint。
**目标**:vitest 单测 + Playwright E2E + i18n(zh/en)+ 暗色模式 + ESLint/Prettier。

**代码改动**:
1. **vitest**:`frontend/src/utils/__tests__/sse.test.ts`(测 CRLF 归一化后的解析)、`markdown.test.ts`、`stores/chat.test.ts`(mock streamChat 测 sendMessage/stop/retry)
2. **Playwright**:`frontend/e2e/chat.spec.ts`(登录→提问→等 token 出现→上传文档)、`admin.spec.ts`;CI 里 `npm run e2e`
3. **i18n**:`src/i18n/zh.ts` + `en.ts`,`vue-i18n` 或轻量 `t()` 函数;抽出各视图硬编码文案
4. **暗色模式**:`main.css` 加 `[data-theme="dark"]` 覆盖(design token 已预留);App.vue 加切换按钮,存 localStorage;各视图 scoped 样式补 dark 变量(颜色已 token 化,改动小)
5. **ESLint + Prettier**:`.eslintrc`/`prettier.config`,`npm run lint` 进 CI

**验收**:`npm test`、`npm run e2e` 全绿;语言/主题切换生效;CI 含 lint。

---

## P1-7 文档完整性

**现状**:README 简陋;**README 引用的两份设计文档在仓库不存在(死链)**;无架构图/部署文档。
**目标**:README 自洽 + docs/ 齐全 + 无死链。

**代码改动**:
1. **修死链**:README 里 `水利行业RAG+Agent_本地版部署清单_优化版.md`、`敲代码前期准备_修订版.md` 二选一:补进 `docs/` 或删掉引用
2. **README 重写**:架构图(Mermaid `graph LR` 请求→编排→Agent→检索→SSE)、功能清单、快速开始、端口/环境变量表、目录结构
3. **docs/ 新增**:
   - `docs/architecture.md`(分层架构 + 请求时序 + 数据流)
   - `docs/api.md`(各端点表 + 鉴权说明,或指向 FastAPI `/docs`)
   - `docs/deployment.md`(Docker 一键、环境变量、备份恢复)
   - `docs/design-decisions.md`(ADR:为何 SQLite/WAL、为何纯内存限流、为何不接 Celery 等——面试/评审都能聊)
4. 代码注释里的 `§x.y` 引用:如文档补齐,保留;否则清理

**验收**:README 无死链、含架构图;docs 四份齐全;`/docs`(FastAPI)可用。

---

## P1-8 RAG 领域深度

**现状**:无检索质量评估;citation 恒真桩;document_qa pipeline 两段独立回答。
**目标**:评估集 + 引用溯源 + pipeline 真串联 + 会话标题。

**代码改动**:
1. **评估集** `data/eval/questions.jsonl`:
   ```json
   {"id":"q1","query":"水库调度原则","expected_docs":["<sha256 前缀>"],"key_points":["防洪","兴利"]}
   ```
   `scripts/eval_rag.py`:跑检索算 `Recall@k`(命中 expected_docs 比例)、`MRR`;引用覆盖率;输出指标表
2. **引用溯源** `backend/rag/citation.py` 实现 `verify_citation`(去掉恒真桩):校验 answer 里 `[n]` 标记对应的 source_id 是否在本次真实检索 evidence 内
3. **pipeline 真串联** `orchestrator.py`:
   - `document_qa` 第一步 document_analysis 的 `structured_output`(文档摘要/要点)写入 context
   - 第二步 knowledge_qa 的 `retrieve_node` 读取该 context 作为检索 query 增强
4. **会话标题生成** `chat.py` done 后:`threads` 表加 `title`,用 LLM 从首条用户消息生成 ≤12 字标题(失败回退首 20 字)
5. **评估报告** `docs/eval-report.md`:跑完存指标

**验收**:`eval_rag` 输出 Recall@k/MRR;citation 非恒真;document_qa 第二步确实用到第一步结果;会话列表显示自动标题。

---

## 推进顺序建议

| 阶段 | 内容 | 工作量 |
|---|---|---|
| 一 | P0-1 测试+CI | 中 |
| 二 | P0-4 分页/错误/限流 | 小 |
| 三 | P0-2 Docker | 小 |
| 四 | P0-3 可观测性 | 小 |
| 五 | P0-5 任务队列 | 中 |
| 六 | P1-7 文档 | 小 |
| 七 | P1-6 前端工程化 | 中 |
| 八 | P1-8 RAG 评估 | 中 |

> 每阶段独立可交付、可验收;做完一个再动下一个,避免大爆炸式改动。
