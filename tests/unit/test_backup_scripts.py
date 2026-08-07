"""G5.2 备份脚本逻辑测试：保留策略裁剪 + 备份自检。"""

from __future__ import annotations

import sqlite3

from backend.config import settings
from scripts import backup_data


def _make_backup_dir(root, stamp: str) -> None:
    (root / f"backup_{stamp}").mkdir(parents=True, exist_ok=True)


def test_prune_removes_old_keeps_recent(tmp_path):
    _make_backup_dir(tmp_path, "20000101_000000")  # 20 年前的备份
    _make_backup_dir(tmp_path, "20260806_120000")  # 今天的备份
    removed = backup_data.prune_backups(tmp_path, retention_days=7)
    assert removed == [tmp_path / "backup_20000101_000000"]
    assert (tmp_path / "backup_20260806_120000").exists()


def test_prune_ignores_unparseable_dirs(tmp_path):
    (tmp_path / "random_dir").mkdir()
    (tmp_path / "MANIFEST.txt").write_text("x")
    assert backup_data.prune_backups(tmp_path, retention_days=1) == []
    assert (tmp_path / "random_dir").exists()


def test_prune_retention_zero_is_noop(tmp_path):
    _make_backup_dir(tmp_path, "20000101_000000")
    assert backup_data.prune_backups(tmp_path, retention_days=0) == []
    assert (tmp_path / "backup_20000101_000000").exists()


def test_validate_backup_ok(tmp_path, monkeypatch):
    db = tmp_path / "water.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO t (name) VALUES ('水利')")
    conn.commit()
    conn.close()
    (tmp_path / "chroma").mkdir()
    (tmp_path / "chroma" / "sqlite3.db").write_text("x")

    monkeypatch.setattr(settings, "SQLITE_PATH", "data/water.db")
    monkeypatch.setattr(settings, "CHROMA_PATH", "data/chroma")
    assert backup_data.validate_backup(tmp_path) == []


def test_validate_backup_missing_db(tmp_path):
    assert any("water.db" in p for p in backup_data.validate_backup(tmp_path))


def test_backup_db_produces_consistent_wal_snapshot(tmp_path):
    """G10.24：在线备份 API 对活动中的 WAL 库产出可直接打开、含已提交数据的一致性快照。

    源连接保持打开：已提交数据未必 checkpoint 回主库（可能仍在 -wal），裸拷贝
    db+-wal+-shm 三件套在此瞬间无法保证快照一致——回归点。
    """
    src = tmp_path / "live.db"
    conn = sqlite3.connect(src)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO t (name) VALUES ('水利-在WAL')")
    conn.commit()
    try:
        dst = tmp_path / "snap.db"
        backup_data._backup_db(src, dst)

        # 快照是可直接独立打开的单文件库：完整性 + 数据都在
        check = sqlite3.connect(f"file:{dst}?mode=ro", uri=True)
        try:
            assert check.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
            assert check.execute("SELECT name FROM t").fetchall() == [("水利-在WAL",)]
        finally:
            check.close()
    finally:
        conn.close()
