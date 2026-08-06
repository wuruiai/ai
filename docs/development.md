# 开发指南

## 环境要求

- Windows 10/11 x64（生产环境 Linux/Docker）
- Python 3.11.x
- Node.js 20.x LTS
- 16 GB 内存（建议）、20 GB SSD（建议）

## 本地启动

```bash
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
copy .env.example .env
# 编辑 .env，填写 DASHSCOPE_API_KEY
start_dev.bat   # 一键启动（后端 8001 + 前端 5173）
```

## 测试

```bash
# 后端单测（mock 掉云端，无需 API Key）
.venv\Scripts\python.exe -m pytest
# 覆盖率（CI 门禁 ≥70%）
.venv\Scripts\python.exe -m pytest --cov=backend --cov-report=term-missing
# 冒烟（需要真实 DashScope Key，验证健康/认证/上传/检索全链路）
.venv\Scripts\python.exe -m scripts.smoke_test
# 前端
cd frontend && npm run type-check && npm run test
```

## 代码规范（ruff，CI 门禁）

```bash
ruff check backend scripts tests   # lint
ruff format --check backend scripts tests  # 格式校验（未格式化即阻断 CI）
ruff format backend scripts tests  # 自动格式化
```

规范要点（完整豁免清单见 `pyproject.toml [tool.ruff]`）：

- `target-version = py311`，`line-length = 100`。
- 默认 F/E/W + UP（modern 注解）+ I（import 排序）+ B（bugbear）+ S（安全）+ DTZ + G + PIE + RUF。
- 中文业务字符串/注释豁免 RUF001/002/003；测试豁免 S101/105/107；脚本豁免 S101/BLE001。
- FastAPI `Depends()` 等依赖注入惯用法列入 `extend-immutable-calls` 白名单。

## CI

`.github/workflows/ci.yml` 在 push/PR 时自动执行：

- **backend**：pytest（覆盖率 ≥70%）→ ruff lint → ruff format check。
- **smoke**：配置了 DashScope Key secret 时跑真实冒烟。
- **frontend**：type-check → 单测 → build。

提交前自查：`ruff check . && ruff format --check . && pytest` 全绿再 push。

## 目录结构

```
water-rag-local/
├── backend/              # FastAPI 后端
│   ├── api/              # REST/SSE 接口
│   ├── core/             # 公共核心（安全/限流/预算/审计/SSE）
│   ├── rag/              # RAG 管线（检索/重排/引用/摄取）
│   ├── db/               # 数据库（连接/迁移）
│   ├── schemas/          # Pydantic 数据契约
│   ├── tasks/            # 摄取任务
│   └── agents/           # LangGraph Agent
├── frontend/             # Vue3 + TS + Pinia 前端
├── scripts/              # 初始化/备份/评测/冒烟脚本
├── tests/                # 单元测试（tests/unit）
├── docs/                 # 文档
│   ├── planning/         # 内部规划文档（分析过程存档）
│   └── *.md              # 公开文档
└── data/                 # 运行时数据（勿提交 Git）
```

## 相关文档

- [架构](architecture.md)
- [API 契约](api.md)
- [部署与运维](deployment.md)
- [安全设计](security.md)
