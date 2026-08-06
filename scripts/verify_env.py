"""环境自检脚本

验证本地开发环境配置是否正确。所有检查均为真实调用，不输出硬编码结果。

Usage:
    python -m scripts.verify_env --config-only
    python -m scripts.verify_env --check-database
    python -m scripts.verify_env --check-cloud
    python -m scripts.verify_env --all
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

import httpx

# 脚本独立运行：未 pip install 时把项目根加入 sys.path，保证 backend 包可直接导入
sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# 配置检查
# ---------------------------------------------------------------------------


def check_config() -> bool:
    """验证 .env 可解析、Key 非空且不泄露。返回是否通过。"""
    print("Checking configuration...")
    from backend.config import get_settings

    s = get_settings()
    key = (s.DASHSCOPE_API_KEY or "").strip()
    masked = (key[:4] + "****" + key[-4:]) if len(key) > 8 else "(empty)"
    print(
        f"OK config: APP_HOST={s.APP_HOST}, APP_PORT={s.APP_PORT}, "
        f"FRONTEND_ORIGIN={s.FRONTEND_ORIGIN}"
    )
    print(f"OK models: LLM={s.LLM_MODEL}, Embedding={s.EMBEDDING_MODEL}, Rerank={s.RERANK_MODEL}")
    print(f"OK key: DASHSCOPE_API_KEY={masked}")
    if not key:
        print("FAIL: DASHSCOPE_API_KEY is empty (fill .env)", file=sys.stderr)
        return False
    return True


# ---------------------------------------------------------------------------
# 数据库检查
# ---------------------------------------------------------------------------


async def _check_database() -> bool:
    import aiosqlite

    from backend.config import settings

    ok = True

    # SQLite 可打开 + WAL 生效
    try:
        async with aiosqlite.connect(settings.SQLITE_PATH) as db:
            cur = await db.execute("PRAGMA journal_mode")
            mode = (await cur.fetchone())[0]
            if mode.lower() == "wal":
                print("OK database: SQLite WAL enabled")
            else:
                print(f"WARN database: journal_mode={mode} (expect wal)")
    except Exception as e:
        ok = False
        print(f"FAIL database: {e}", file=sys.stderr)

    # FTS5 可用
    try:
        async with aiosqlite.connect(settings.SQLITE_PATH) as db:
            await db.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS _verify_fts "
                "USING fts5(content, tokenize='trigram')"
            )
            await db.execute("INSERT INTO _verify_fts(content) VALUES (?)", ("水利工程测试",))
            cur = await db.execute(
                "SELECT rowid FROM _verify_fts WHERE _verify_fts MATCH ?", ("水利工",)
            )
            rows = await cur.fetchall()
            await db.execute("DROP TABLE _verify_fts")
            await db.commit()
            if rows:
                print("OK FTS5: trigram tokenizer works for Chinese")
            else:
                print("FAIL FTS5: trigram query returned no match")
                ok = False
    except Exception as e:
        ok = False
        print(f"FAIL FTS5: {e}", file=sys.stderr)

    # Chroma 可打开
    try:
        import chromadb

        client = chromadb.PersistentClient(path=settings.CHROMA_PATH)
        client.get_or_create_collection(settings.CHROMA_COLLECTION)
        print("OK Chroma: collection accessible")
    except Exception as e:
        ok = False
        print(f"FAIL Chroma: {e}", file=sys.stderr)

    return ok


# ---------------------------------------------------------------------------
# 云服务检查
# ---------------------------------------------------------------------------


async def _check_cloud() -> bool:
    from backend.config import settings

    if not settings.DASHSCOPE_API_KEY:
        print("FAIL cloud: DASHSCOPE_API_KEY is empty", file=sys.stderr)
        return False

    ok = True

    # LLM
    try:
        from langchain_core.messages import HumanMessage

        from backend.core.model_factory import ModelFactory

        t0 = time.time()
        llm = ModelFactory.create_llm(temperature=0.0)
        resp = await llm.ainvoke([HumanMessage(content="请只回复两个字：正常")])
        dt = (time.time() - t0) * 1000
        if resp and getattr(resp, "content", ""):
            print(f"OK LLM: {settings.LLM_MODEL} ({dt:.0f}ms)")
        else:
            print(f"FAIL LLM: empty response ({dt:.0f}ms)")
            ok = False
    except Exception as e:
        ok = False
        print(f"FAIL LLM: {type(e).__name__}: {e}", file=sys.stderr)

    # Embedding
    try:
        from backend.rag.embedding import get_embedding

        t0 = time.time()
        vec = await get_embedding("测试")
        dt = (time.time() - t0) * 1000
        if vec and len(vec) > 0:
            print(f"OK Embedding: {settings.EMBEDDING_MODEL} dim={len(vec)} ({dt:.0f}ms)")
        else:
            print(f"FAIL Embedding: empty vector ({dt:.0f}ms)")
            ok = False
    except Exception as e:
        ok = False
        print(f"FAIL Embedding: {type(e).__name__}: {e}", file=sys.stderr)

    # Rerank
    try:
        from backend.rag.reranker import rerank

        t0 = time.time()
        scores = await rerank("水利工程", ["水利工程是治理水患的工程", "今天天气很好"], top_k=2)
        dt = (time.time() - t0) * 1000
        if scores:
            print(f"OK Rerank: {settings.RERANK_MODEL} ({dt:.0f}ms)")
        else:
            print(f"FAIL Rerank: no results ({dt:.0f}ms)")
            ok = False
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 403:
            # 账号未开通该模型权限：这是账号配置问题，不算实现失败
            print(
                f"WARN Rerank: {settings.RERANK_MODEL} returned 403 "
                f"(账号未开通权限，请在百炼控制台授权；功能本身可降级)"
            )
        else:
            ok = False
            print(f"FAIL Rerank: HTTP {e.response.status_code}: {e}", file=sys.stderr)
    except Exception as e:
        ok = False
        print(f"FAIL Rerank: {type(e).__name__}: {e}", file=sys.stderr)

    return ok


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="环境自检脚本")
    parser.add_argument("--config-only", action="store_true", help="仅检查配置")
    parser.add_argument("--check-database", action="store_true", help="检查数据库")
    parser.add_argument("--check-cloud", action="store_true", help="检查云服务")
    parser.add_argument("--all", action="store_true", help="执行所有检查")
    args = parser.parse_args()

    if args.all:
        ok = check_config()
        ok = asyncio.run(_check_database()) and ok
        ok = asyncio.run(_check_cloud()) and ok
        print(f"\nResult: {'ALL OK' if ok else 'SOME CHECKS FAILED'}")
        return 0 if ok else 1
    if args.config_only:
        return 0 if check_config() else 1
    if args.check_database:
        return 0 if asyncio.run(_check_database()) else 1
    if args.check_cloud:
        return 0 if asyncio.run(_check_cloud()) else 1
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
