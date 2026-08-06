"""数据备份脚本

备份数据库和向量库数据到 backups/ 下带时间戳的目录。

Usage:
    python -m scripts.backup_data
    python -m scripts.backup_data --note pre-upgrade

备份内容：
    - data/water.db (+ -wal / -shm 一并拷贝，保证一致性)
    - data/chroma/ 向量库目录

安全注意：
    - 不拷贝 data/source/（原始文件体积大且可重新导入）
    - 不拷贝日志
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.config import settings


def main() -> int:
    parser = argparse.ArgumentParser(description="数据备份")
    parser.add_argument("--note", default="", help="备份备注（可空）")
    args = parser.parse_args()

    backup_root = Path(settings.BACKUP_PATH)
    backup_root.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    note = f"_{args.note}" if args.note else ""
    target = backup_root / f"backup_{stamp}{note}"
    target.mkdir(parents=True, exist_ok=False)

    copied: list[str] = []

    # 1. SQLite 数据库（含 WAL/SHM，保证一致性）
    db = Path(settings.SQLITE_PATH)
    if db.exists():
        shutil.copy2(db, target / db.name)
        copied.append(db.name)
        for suffix in ("-wal", "-shm"):
            extra = Path(str(db) + suffix)
            if extra.exists():
                shutil.copy2(extra, target / extra.name)
                copied.append(extra.name)

    # 2. Chroma 向量库目录
    chroma_dir = Path(settings.CHROMA_PATH)
    if chroma_dir.exists():
        shutil.copytree(chroma_dir, target / chroma_dir.name, dirs_exist_ok=True)
        copied.append(f"{chroma_dir.name}/")

    if not copied:
        print("WARN: 未发现可备份的数据（water.db / chroma 均不存在）")
        return 1

    # 写备份清单
    manifest = target / "MANIFEST.txt"
    manifest.write_text(
        "\n".join(
            [
                f"backup_time: {stamp}",
                f"note: {args.note or '-'}",
                f"items: {', '.join(copied)}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    copied.append("MANIFEST.txt")

    print(f"OK 备份完成: {target}")
    print(f"   包含: {', '.join(copied)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
