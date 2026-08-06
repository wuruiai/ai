"""数据备份脚本

备份数据库和向量库数据到 backups/ 下带时间戳的目录，并做备份后自检与保留策略裁剪。

Usage:
    python -m scripts.backup_data
    python -m scripts.backup_data --note pre-upgrade
    python -m scripts.backup_data --retention-days 7

备份内容：
    - data/water.db (+ -wal / -shm 一并拷贝，保证一致性)
    - data/chroma/ 向量库目录

备份后自检：
    - SQLite `PRAGMA integrity_check` 必须返回 ok
    - Chroma 目录必须非空

保留策略：
    - 超过 --retention-days 的旧备份目录会被删除（默认取 settings.BACKUP_RETENTION_DAYS）

安全注意：
    - 不拷贝 data/source/（原始文件体积大且可重新导入）
    - 不拷贝日志
"""

from __future__ import annotations

import argparse
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# 脚本独立运行：未 pip install 时把项目根加入 sys.path，保证 backend 包可直接导入
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.config import settings

# 备份目录前缀：backup_YYYYmmdd_HHMMSS[_note]
_BACKUP_NAME_RE = re.compile(r"^backup_(\d{8}_\d{6})")


def _parse_backup_stamp(name: str) -> datetime | None:
    """从备份目录名解析时间戳，无法解析返回 None。"""
    m = _BACKUP_NAME_RE.match(name)
    if not m:
        return None
    try:
        # 备份名由 datetime.now()（本地时间）生成，这里补上本地时区便于与截止时间比较
        tz = datetime.now().astimezone().tzinfo
        return datetime.strptime(m.group(1), "%Y%m%d_%H%M%S").replace(tzinfo=tz)
    except ValueError:
        return None


def validate_backup(target: Path) -> list[str]:
    """备份后自检：DB integrity_check + Chroma 非空。返回问题列表（空 = 通过）。"""
    problems: list[str] = []

    db = target / Path(settings.SQLITE_PATH).name
    if db.exists():
        # 只读连接跑完整性校验，避免误开 WAL/写日志
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            rows = conn.execute("PRAGMA integrity_check").fetchall()
            if rows != [("ok",)]:
                problems.append(f"water.db integrity_check 异常: {rows}")
        finally:
            conn.close()
    else:
        problems.append("备份中缺少 water.db")

    chroma_dir = target / Path(settings.CHROMA_PATH).name
    if chroma_dir.exists() and not any(chroma_dir.iterdir()):
        problems.append("chroma 目录为空")

    return problems


def prune_backups(backup_root: Path, retention_days: int) -> list[Path]:
    """删除超过保留天数的备份目录，返回被删除的目录列表。"""
    if retention_days <= 0:
        return []
    cutoff = datetime.now().timestamp() - retention_days * 86400
    removed: list[Path] = []
    for entry in sorted(backup_root.iterdir()):
        if not entry.is_dir():
            continue
        stamp = _parse_backup_stamp(entry.name)
        if stamp is None:
            continue
        if stamp.timestamp() < cutoff:
            shutil.rmtree(entry)
            removed.append(entry)
    return removed


def run_backup(note: str = "", retention_days: int | None = None) -> int:
    """执行一次备份：拷贝 DB + Chroma，自检，裁剪过期备份。

    与 CLI 解耦，便于被 backup_cron.py 等脚本复用（不走 argv 解析）。
    """
    if retention_days is None:
        retention_days = settings.BACKUP_RETENTION_DAYS

    backup_root = Path(settings.BACKUP_PATH)
    backup_root.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = backup_root / f"backup_{stamp}{f'_{note}' if note else ''}"
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

    # 3. 备份后自检
    problems = validate_backup(target)
    if problems:
        print("FAIL 备份自检未通过，备份目录将删除：")
        for p in problems:
            print(f"  - {p}")
        shutil.rmtree(target)
        return 1

    # 4. 写备份清单
    manifest = target / "MANIFEST.txt"
    manifest.write_text(
        "\n".join(
            [
                f"backup_time: {stamp}",
                f"note: {note or '-'}",
                f"items: {', '.join(copied)}",
                "integrity_check: ok",
                "",
            ]
        ),
        encoding="utf-8",
    )
    copied.append("MANIFEST.txt")

    # 5. 保留策略裁剪
    removed = prune_backups(backup_root, retention_days)
    print(f"OK 备份完成: {target}")
    print(f"   包含: {', '.join(copied)}")
    if removed:
        print(f"   清理过期备份 ({retention_days} 天): {', '.join(str(r) for r in removed)}")
    else:
        print(f"   保留策略: 无过期备份（保留 {retention_days} 天）")
    return 0


def main() -> int:
    """CLI 入口：解析命令行后调用 run_backup。"""
    parser = argparse.ArgumentParser(description="数据备份（含自检与保留策略）")
    parser.add_argument("--note", default="", help="备份备注（可空）")
    parser.add_argument(
        "--retention-days",
        type=int,
        default=settings.BACKUP_RETENTION_DAYS,
        help="保留天数，超过的旧备份会被删除（<=0 表示不清理）",
    )
    args = parser.parse_args()
    return run_backup(note=args.note, retention_days=args.retention_days)


if __name__ == "__main__":
    sys.exit(main())
