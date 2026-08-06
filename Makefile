# 统一命令入口（CI 与 Linux/容器环境；本地 Windows 用 start_dev.bat 一键起服）
# 示例：make test  /  make lint  /  make docker-up
.PHONY: help install setup test test-cov lint format format-check \
        run-backend smoke eval docker-up docker-down build

help: ## 列出可用命令
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-14s %s\n", $$1, $$2}'

install: ## 安装依赖（冻结版本 + 开发依赖）
	python -m pip install --upgrade pip
	pip install -r requirements.lock.txt -r requirements-dev.txt

setup: install ## 初始化（venv 创建由用户/CI 完成，这里只装依赖）

test: ## 后端单测（mock 云端，无需 API Key）
	python -m pytest

test-cov: ## 单测 + 覆盖率（CI 门禁 ≥70%）
	python -m pytest --cov=backend --cov-fail-under=70

lint: ## ruff lint（CI 门禁）
	ruff check backend scripts tests

format: ## ruff 自动格式化
	ruff format backend scripts tests

format-check: ## ruff 格式校验（CI 门禁）
	ruff format --check backend scripts tests

run-backend: ## 本地起后端（uvicorn，8001）
	uvicorn backend.main:app --host 127.0.0.1 --port 8001 --reload

smoke: ## 冒烟测试（需要 DASHSCOPE_API_KEY）
	python -m scripts.smoke_test

eval: ## RAG 检索质量评测（纯检索，不调 LLM）
	python -m scripts.evaluate_rag

docker-up: ## 开发形态：构建并启动（前端 :80 / 后端 :8001）
	docker compose up -d --build

docker-down: ## 停止开发栈
	docker compose down

build: ## 构建生产镜像（VERSION 覆盖 tag，默认 latest）
	docker build -t ghcr.io/water-rag/water-rag-backend:$${VERSION:-latest} .
	docker build -t ghcr.io/water-rag/water-rag-frontend:$${VERSION:-latest} ./frontend
