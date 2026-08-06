# 部署与运维

## Docker

### 开发形态（本地构建）

```bash
docker compose up -d --build
# 前端 http://localhost:80 ，后端 http://localhost:8001
```

开发清单 `docker-compose.yml` 本地构建两个镜像：后端（python:3.11-slim + 清华 pip 镜像）、
前端（node:20 多阶段构建 → nginx，内置 `frontend/.npmrc` 国内镜像规避官方源卡顿）。

### 生产形态（预构建镜像）

生产清单 `docker-compose.prod.yml` 与开发文件**相互独立**（不合并覆盖），引用
`ghcr.io/water-rag/water-rag-backend:${VERSION:-latest}`：

```bash
# 1. 构建并推送镜像（VERSION 指定 tag，默认 latest）
docker build -t ghcr.io/water-rag/water-rag-backend:${VERSION:-latest} .
docker build -t ghcr.io/water-rag/water-rag-frontend:${VERSION:-latest} ./frontend
docker push ghcr.io/water-rag/water-rag-backend:${VERSION:-latest}
docker push ghcr.io/water-rag/water-rag-frontend:${VERSION:-latest}

# 2. 部署机拉取并启动
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d

# 3. 回滚：换 VERSION 再 up 即可
```

生产清单特性：

- 强制 `APP_ENV=production` → 后端 `ensure_secrets()` 缺密钥 **fail-fast** 拒绝启动。
- `restart: always`，数据用命名卷持久化。
- 健康检查走 `/health/ready` 就绪探针（SQLite + Chroma 均可达才 healthy）。
- 密钥经宿主环境变量 / `.env.production` 注入（已被 `.gitignore` 排除，不入库）。
- 后端镜像启动 `python -m scripts.init_db && uvicorn`，自动初始化数据库与迁移。

## 备份与定时任务

备份自动校验（`PRAGMA integrity_check` + Chroma 非空）并按保留天数清理旧备份，默认保留 7 天：

```bash
# 手动备份（带备注）
python -m scripts.backup_data --note pre-upgrade
# 指定保留天数
python -m scripts.backup_data --retention-days 14
```

定时备份用 `scripts/backup_cron.py`，由 `BACKUP_ENABLED`（默认 true）控制开关：

```bash
python -m scripts.backup_cron --once            # 单次（Windows 计划任务 / cron 每日触发）
python -m scripts.backup_cron --interval-hours 3  # 常驻（docker sidecar / systemd / NSSM）
```

Windows 计划任务示例：

```bash
schtasks /Create /SC DAILY /ST 02:00 /TN "water-rag-backup" /TR "cd /d <项目根目录> && .venv\Scripts\python -m scripts.backup_cron --once"
```

Docker 定时备份（复用数据卷）：

```bash
docker run -d --name water-backup-cron -v water-data:/app/data <镜像> python -m scripts.backup_cron --interval-hours 3
```

## 环境变量清单

配置中心在 `backend/config.py`（pydantic-settings），模板见 `.env.example`。核心变量：

| 变量 | 默认 | 说明 |
|------|------|------|
| `APP_ENV` | `local` | 非 `local`（production/staging 等）触发密钥 fail-fast 校验 |
| `APP_PORT` | `8001` | 后端监听端口 |
| `TOKEN_SECRET` | 空 | JWT 签名密钥；**生产必填**（缺失拒绝启动） |
| `DASHSCOPE_API_KEY` | 空 | 云端模型密钥；**生产必填** |
| `LLM_MODEL` / `LLM_MODEL_HARD` | `qwen-plus` / `qwen-max` | 常规 / 复杂任务模型 |
| `EMBEDDING_MODEL` / `EMBEDDING_DIM` | `text-embedding-v3` / `1024` | 向量模型与维度 |
| `RERANK_MODEL` | `gte-rerank` | 精排模型（账号未开通自动降级） |
| `SQLITE_PATH` / `CHROMA_PATH` | `./data/*` | 本地数据路径 |
| `REDIS_URL` | 空 | 多实例限流/预算共享（填 `redis://` 启用） |
| `RATE_LIMIT_PER_MINUTE` | `30` | 每用户每分钟请求上限 |
| `DAILY_CALL_LIMIT` | `1000` | 每用户每日调用预算 |
| `BACKUP_ENABLED` / `BACKUP_RETENTION_DAYS` | `true` / `7` | 备份开关与保留天数 |

版本号不在此控制：单一来源 `backend/__init__.py.__version__`，health / OpenAPI 自动跟随（仅灰度/品牌场景才用 `APP_VERSION` 环境变量覆盖）。

## 就绪探针

`/health` 存活；`/health/ready` 就绪（校验 SQLite + Chroma 均可达）。生产探针用 `/health/ready`。

## 相关文档

- [架构](architecture.md)
- [安全设计](security.md)
