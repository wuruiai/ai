"""灌测试数据脚本

创建一份水利演示文档并走完整摄取管线（分块 → embedding → Chroma），
让知识库有可提问的数据。重复运行幂等：同一文件（同 sha256）重新触发摄取。

Usage:
    python -m scripts.seed_demo

注意：摄取需要 DashScope 可用（.env 里的 DASHSCOPE_API_KEY + embedding 模型）。
无 Key/网络失败时会如实报告 status=failed，不再假打印成功。
"""

import asyncio
import hashlib
import sys
from pathlib import Path

# 脚本独立运行：未 pip install 时把项目根加入 sys.path，保证 backend 包可直接导入
sys.path.insert(0, str(Path(__file__).parent.parent))

DEMO_TEXT = """水利工程是用于控制和调配自然界的地表水和地下水，达到除害兴利目的而修建的工程。
水利工程的主要功能包括防洪、灌溉、供水、发电、航运和水土保持等。常见的工程类型有水库、堤防、水闸、泵站、渠道和分洪区。
水库调度需要在防洪与兴利之间统筹兼顾。汛期以安全度汛为主，通过泄洪调度控制库水位；汛后蓄水兴利，保障供水、灌溉和发电的用水需求。
防汛应急预案应明确预警分级、指挥体系、物资储备和撤离路线，并定期组织演练。工程安全是水利管理的基本底线，需定期进行大坝安全鉴定。
灌溉用水效率直接关系到粮食安全，应推广节水灌溉技术，合理确定灌溉定额，防止土壤次生盐碱化。
"""


async def seed() -> int:
    from backend.config import settings
    from backend.db.connection import close_db, close_pool, get_connection
    from backend.tasks.ingestion_worker import IngestionStatus, ingest_document

    source_dir = Path(settings.SOURCE_PATH)
    source_dir.mkdir(parents=True, exist_ok=True)
    file_path = source_dir / "demo_水利基础.txt"
    content = DEMO_TEXT.encode("utf-8")
    file_path.write_bytes(content)

    document_id = hashlib.sha256(content).hexdigest()

    # 幂等入库（同 hash 重复插入被忽略）
    db = await get_connection()
    try:
        cur = await db.execute(
            "INSERT OR IGNORE INTO documents "
            "(document_id, file_name, stored_path, file_hash, file_size, mime_type, "
            " document_title, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                document_id,
                file_path.name,
                str(file_path),
                document_id,
                len(content),
                "text/plain",
                "水利基础演示文档",
                IngestionStatus.PENDING.value,
            ),
        )
        await db.commit()
        inserted = cur.rowcount
    finally:
        await close_db(db)

    status = await ingest_document(file_path, document_id)
    # G10.24：摄取完成后关闭连接池，避免 aiosqlite 后台线程拖住解释器不退出
    await close_pool()
    print(f"Demo document: {file_path.name} (document_id={document_id[:16]}...)")
    print(f"  insert={'new' if inserted else 'existing, re-seeded'}  status={status.value}")
    if status != IngestionStatus.READY:
        print(
            "WARN: ingestion did not reach ready — 检查 DASHSCOPE_API_KEY 与网络", file=sys.stderr
        )
        return 1
    return 0


def main():
    """插入演示数据"""
    print("Seeding demo data...")
    sys.exit(asyncio.run(seed()))


if __name__ == "__main__":
    main()
