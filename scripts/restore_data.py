"""数据恢复脚本

从备份恢复数据库和向量库数据。

Usage:
    python -m scripts.restore_data --backup-path <path>
    python -m scripts.restore_data --list          # 列出可用备份

流程：
    1. 校验备份目录存在且含 water.db（或 chroma/）
    2. 先备份当前数据（自动 fallback），防止误恢复
    3. 用备份文件覆盖 data/ 下的 water.db / chroma
    4. 提示重启后端生效

安全：
    - 恢复前自动做一次当前数据备份（restore_before_*）
    - 不删除当前 data/source（原始文件保留）
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


def _list_backups() -> None:
    backup_root = Path(settings.BACKUP_PATH)
    if not backup_root.exists():
        print("（无备份目录）")
        return
    for d in sorted(backup_root.glob("backup_*")):
        has_db = (d / "water.db").exists()
        has_chroma = (d / "chroma").exists()
        marker = []
        if has_db:
            marker.append("db")
        if has_chroma:
            marker.append("chroma")
        print(f"  {d.name}  [{' + '.join(marker)}]")


def main() -> int:
    parser = argparse.ArgumentParser(description="数据恢复")
    parser.add_argument("--backup-path", help="备份目录路径")
    parser.add_argument("--list", action="store_true", help="列出可用备份")
    args = parser.parse_args()

    if args.list:
        _list_backups()
        return 0

    if not args.backup_path:
        parser.print_help()
        return 2

    backup = Path(args.backup_path)
    if not backup.is_dir():
        print(f"FAIL: 备份目录不存在: {backup}", file=sys.stderr)
        return 1

    # 校验备份内容
    src_db = backup / "water.db"
    src_chroma = backup / "chroma"
    if not src_db.exists() and not src_chroma.is_dir():
        print("FAIL: 备份中既无 water.db 也无 chroma/，无法恢复", file=sys.stderr)
        return 1

    # 恢复前先备份当前数据（防误操作）
    pre = Path(settings.BACKUP_PATH) / f"restore_before_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    pre.mkdir(parents=True, exist_ok=True)
    cur_db = Path(settings.SQLITE_PATH)
    if cur_db.exists():
        shutil.copy2(cur_db, pre / "water.db")
        for suffix in ("-wal", "-shm"):
            extra = Path(str(cur_db) + suffix)
            if extra.exists():
                shutil.copy2(extra, pre / extra.name)
    cur_chroma = Path(settings.CHROMA_PATH)
    if cur_chroma.exists():
        shutil.copytree(cur_chroma, pre / "chroma", dirs_exist_ok=True)
    print(f"  当前数据已暂存: {pre}")

    # 恢复 DB
    if src_db.exists():
        # 先清 WAL/SHM（残留可能干扰）
        for suffix in ("-wal", "-shm"):
            Path(str(cur_db) + suffix).unlink(missing_ok=True)
        shutil.copy2(src_db, cur_db)
        # 备份若含 WAL/SHM（活库备份时未 checkpoint 的事务），一并拷回，
        # 否则这些最近写入会在恢复时静默丢失
        for suffix in ("-wal", "-shm"):
            extra = backup / f"water.db{suffix}"
            if extra.exists():
                shutil.copy2(extra, Path(str(cur_db) + suffix))
        print("  OK water.db 已恢复（含 WAL/SHM）")

    # 恢复 chroma
    if src_chroma.is_dir():
        if cur_chroma.exists():
            shutil.rmtree(cur_chroma)
        shutil.copytree(src_chroma, cur_chroma)
        print("  OK chroma/ 已恢复")

    print("恢复完成。请重启后端服务使数据生效。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
