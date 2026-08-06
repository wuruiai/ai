"""Chroma 集合初始化脚本

初始化 Chroma 向量数据库集合。

Usage:
    python -m scripts.init_chroma
"""

import sys
from pathlib import Path

# 脚本独立运行：未 pip install 时把项目根加入 sys.path，保证 backend 包可直接导入
sys.path.insert(0, str(Path(__file__).parent.parent))


def main():
    """初始化 Chroma 集合（与 backend/main.py lifespan 的启动逻辑一致）"""
    import chromadb

    from backend.config import settings

    print("Initializing Chroma collection...")
    Path(settings.CHROMA_PATH).mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=settings.CHROMA_PATH)
    client.get_or_create_collection(settings.CHROMA_COLLECTION)
    print(f"OK Chroma collection '{settings.CHROMA_COLLECTION}' ready at {settings.CHROMA_PATH}")


if __name__ == "__main__":
    main()
