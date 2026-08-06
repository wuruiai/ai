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

# 关闭 Chroma 遥测：容器无外网时会刷 "Failed to send telemetry event ..." 噪声
ENV ANONYMIZED_TELEMETRY=False

# 启动前自动跑迁移（幂等）
EXPOSE 8001
CMD ["sh", "-c", "python -m scripts.init_db && python -m uvicorn backend.main:app --host 0.0.0.0 --port 8001 --log-level info"]

# 健康检查：供 docker-compose depends_on + 外部探活使用
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=3)"
