"""单一配置中心

使用 pydantic-settings 管理所有配置项。

Reference: §3.2
"""

from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """应用配置"""

    # 应用基础
    APP_ENV: str = "local"
    APP_HOST: str = "127.0.0.1"
    APP_PORT: int = 8001
    # 应用版本（G7.2 收口）：health 探针 / OpenAPI 统一从这里读，避免硬编码漂移
    APP_VERSION: str = "1.0.0"
    # token 签名 secret：生产必须设置（ensure_secrets 强制），
    # 留空且非生产时进程级随机（重启后旧 token 失效，仅限开发）
    TOKEN_SECRET: str = ""
    # 短时效 access token 有效期（秒）：默认 30 分钟
    ACCESS_TOKEN_TTL_S: int = 1800
    # 长时效 refresh token 有效期（秒）：默认 7 天
    REFRESH_TOKEN_TTL_S: int = 604800
    # 登录防爆破：窗口内最大失败次数，超过后锁定该 key（ip/用户名）lockout 秒
    LOGIN_MAX_FAILURES: int = 5
    LOGIN_LOCKOUT_S: int = 900
    LOGIN_WINDOW_S: int = 900
    LOG_LEVEL: str = "INFO"

    def ensure_secrets(self) -> None:
        """生产环境启动强校验：缺失关键密钥直接拒绝启动（fail-fast）。

        本地/开发环境不强制，便于开箱即用（TOKEN_SECRET 空时进程级随机）。
        """
        if self.APP_ENV != "production":
            return
        secrets = (
            ("TOKEN_SECRET", self.TOKEN_SECRET),
            ("DASHSCOPE_API_KEY", self.DASHSCOPE_API_KEY),
        )
        missing = [name for name, val in secrets if not val]
        if missing:
            raise RuntimeError(
                "production startup blocked: missing required secrets: "
                f"{', '.join(missing)}. 请在 .env 中设置后重启。"
            )

    DATA_ROOT: str = "./data"
    # 允许的前端 Origin，逗号分隔。默认同时允许 localhost 与 127.0.0.1：
    # 开发机 Vite(host:0.0.0.0) 两个地址都能访问，两个 Origin 都会被浏览器带上。
    FRONTEND_ORIGIN: str = "http://127.0.0.1:5173,http://localhost:5173"
    # 额外允许的 Origin（逗号分隔）。生产留空；smoke_test / 灰度场景按需注入。
    # 用法：EXTRA_ALLOWED_ORIGINS="http://127.0.0.1:8123,http://staging.example.com"
    EXTRA_ALLOWED_ORIGINS: str = ""

    @property
    def allowed_origins(self) -> set[str]:
        """全部允许的 Origin（FRONTEND_ORIGIN + EXTRA_ALLOWED_ORIGINS，均支持逗号分隔）。

        CORS / auth / security 三处统一从这里取，避免白名单不一致。
        """
        origins: set[str] = set()
        for raw in (self.FRONTEND_ORIGIN, self.EXTRA_ALLOWED_ORIGINS):
            for o in raw.split(","):
                o = o.strip()
                if o:
                    origins.add(o)
        return origins

    # DashScope 云服务
    DASHSCOPE_API_KEY: str = ""
    # 兼容 OpenAI 协议端点（LLM / Embedding 统一走这里；G7.2 收口避免硬编码）
    DASHSCOPE_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    # DashScope 原生 Rerank 端点（与 base_url 协议不同，单独配置）
    DASHSCOPE_RERANK_URL: str = (
        "https://dashscope.aliyuncs.com/api/v1/services/reranking/text-reranking/text-reranking"
    )
    LLM_MODEL: str = "qwen-plus"
    LLM_MODEL_HARD: str = "qwen-max"
    EMBEDDING_MODEL: str = "text-embedding-v3"
    EMBEDDING_DIM: int = 1024
    RERANK_MODEL: str = "gte-rerank"

    # 超时/重试/预算
    HTTP_TRUST_ENV: bool = False
    LLM_TIMEOUT_S: int = 60
    MAX_RETRIES: int = 2
    MAX_AGENT_STEPS: int = 12
    MAX_MULTI_QUERIES: int = 3
    DAILY_CALL_LIMIT: int = 1000
    # LLM 成本核算（G3.1）：单价 元 / 百万 token，用于用量记账换算成本
    LLM_PRICE_INPUT_PER_M: float = 0.8
    LLM_PRICE_OUTPUT_PER_M: float = 2.0
    # 每用户每分钟请求上限（chat/upload 等重端点）
    RATE_LIMIT_PER_MINUTE: int = 30

    # 本地数据路径
    SQLITE_PATH: str = "./data/water.db"
    # SQLite 连接池大小（G4.2）；<=1 时禁用池化（测试用 0）
    DB_POOL_SIZE: int = 5
    CHROMA_PATH: str = "./data/chroma"
    CHROMA_COLLECTION: str = "water_knowledge"
    SOURCE_PATH: str = "./data/source"
    LOG_PATH: str = "./data/logs"
    BACKUP_PATH: str = "./data/backups"
    # 备份自动化（G5.2）：定时任务/计划程序运行时跳过、保留天数
    BACKUP_ENABLED: bool = True
    BACKUP_RETENTION_DAYS: int = 7

    # 跨进程状态后端（G5.3）：留空用内存实现（单进程）；填 redis:// 换 Redis
    REDIS_URL: str = ""

    # 摄取任务持久化队列（G4.1）
    # 应用进程内跑一个 asyncio worker（本地默认）；生产可另起 scripts.worker 多进程
    INGESTION_WORKER_IN_PROCESS: bool = True
    INGESTION_QUEUE_POLL_SECONDS: float = 1.0
    # 任务租约（秒）：claimed 后超过该时长未完成视为 worker 崩溃，启动时回收
    INGESTION_TASK_LEASE_SECONDS: int = 600
    # 单个任务最大尝试次数，超限标 failed（不再自动重试）
    INGESTION_MAX_ATTEMPTS: int = 3

    # 检索初始参数
    DENSE_TOP_K: int = 30
    BM25_TOP_K: int = 30
    DENSE_WEIGHT: float = 0.7
    SPARSE_WEIGHT: float = 0.3
    RERANK_TOP_K: int = 8

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    """获取配置单例"""
    return Settings()


settings = get_settings()
