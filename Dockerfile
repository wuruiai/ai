# 水利 RAG + Agent 后端镜像
FROM python:3.11-slim

WORKDIR /app

# 安装依赖（利用缓存层；国内网络用清华镜像加速）
COPY requirements.lock.txt .
RUN pip install --no-cache-dir -r requirements.lock.txt \
    -i https://pypi.tuna.tsinghua.edu.cn/simple

# 拷贝源码
COPY backend/ backend/
COPY scripts/ scripts/

# G10.10 M8 非 root 运行（纵深防御基线）：创建专用用户，数据/日志目录归其所有。
# 说明：prod 用命名卷 water_data，首挂载会继承镜像内 /app/data 的属主（appuser）；
# 本地 bind mount（./data）时需宿主目录对 uid 1000 可写（Docker Desktop 默认即可）。
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app

# 关闭 Chroma 遥测：容器无外网时会刷 "Failed to send telemetry event ..." 噪声
ENV ANONYMIZED_TELEMETRY=False

USER appuser

# 启动前自动跑迁移（幂等）
EXPOSE 8001
CMD ["sh", "-c", "python -m scripts.init_db && python -m uvicorn backend.main:app --host 0.0.0.0 --port 8001 --log-level info"]

# 健康检查（就绪探针）：SQLite + Chroma 均可达才 healthy，
# 供 docker-compose depends_on + 编排系统摘流使用
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health/ready', timeout=3)"
