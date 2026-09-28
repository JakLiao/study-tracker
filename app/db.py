"""SQLite 单文件连接 + 建表.

设计原则:
- 单文件 SQLite, 无迁移工具 (v1 表结构稳定)
- WAL 模式: 并发读 + 单写不阻塞
- 外键开启
- 行用 sqlite3.Row, 默认 dict-like
- 上下文管理器式 get_conn()
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from . import config


# DDL 一次性建表, v1 不引入 alembic / 迁移
_SCHEMA = """
CREATE TABLE IF NOT EXISTS assignments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    subject         TEXT NOT NULL CHECK (subject IN ('语文','数学','英语')),
    task_name       TEXT NOT NULL,
    due_date        TEXT NOT NULL,           -- ISO date YYYY-MM-DD
    deadline_time   TEXT,                    -- ISO datetime or NULL
    is_optional     INTEGER NOT NULL DEFAULT 0,  -- 0/1
    est_minutes     INTEGER NOT NULL CHECK (est_minutes BETWEEN 1 AND 180),
    actual_minutes  INTEGER CHECK (actual_minutes IS NULL OR actual_minutes BETWEEN 0 AND 240),
    status          TEXT NOT NULL DEFAULT 'todo' CHECK (status IN ('todo','doing','done','skipped')),
    task_type       TEXT,
    source_span     TEXT NOT NULL,
    confidence      TEXT NOT NULL CHECK (confidence IN ('high','medium','low')),
    batch_id        INTEGER,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    FOREIGN KEY (batch_id) REFERENCES parsed_batches(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_assignments_due_date ON assignments(due_date);
CREATE INDEX IF NOT EXISTS idx_assignments_subject_due ON assignments(subject, due_date);
CREATE INDEX IF NOT EXISTS idx_assignments_status ON assignments(status);

CREATE TABLE IF NOT EXISTS parsed_batches (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_text            TEXT NOT NULL,
    raw_text_hash       TEXT NOT NULL UNIQUE,
    parsed_at           TEXT NOT NULL,
    ai_model_version    TEXT NOT NULL,
    result_count        INTEGER NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','confirmed','discarded')),
    note                TEXT
);

CREATE INDEX IF NOT EXISTS idx_batches_hash ON parsed_batches(raw_text_hash);

CREATE TABLE IF NOT EXISTS completion_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    assignment_id   INTEGER NOT NULL,
    action          TEXT NOT NULL CHECK (action IN ('created','status_changed','actual_logged','edited','deleted')),
    from_value      TEXT,
    to_value        TEXT,
    at              TEXT NOT NULL,
    FOREIGN KEY (assignment_id) REFERENCES assignments(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_logs_assignment_at ON completion_logs(assignment_id, at);

CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""


def init_db(path: Path | None = None) -> None:
    """初始化数据库 (启动时调用一次)."""
    db_path = path or config.DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(_SCHEMA)
        # 完整性快速验证 (避免启动后才报错)
        result = conn.execute("PRAGMA integrity_check").fetchone()
        if result is None or result[0] != "ok":
            raise RuntimeError(f"Database integrity check failed: {result}")


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """获取连接.

    - isolation_level = "" (deferred 模式): 配合 service 层手写 BEGIN/COMMIT
    - row_factory = sqlite3.Row, dict-like
    - 退出时若仍在事务中, 自动 ROLLBACK (兼容 service 层在 raise 之前未 COMMIT 的情况)
    """
    conn = sqlite3.connect(
        config.DB_PATH,
        detect_types=sqlite3.PARSE_DECLTYPES,
        isolation_level="",
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    except Exception:
        # 容错: 若没有活跃事务, ROLLBACK 会抛 OperationalError, 吞掉即可
        try:
            conn.execute("ROLLBACK")
        except sqlite3.OperationalError:
            pass
        raise
    finally:
        conn.close()


def apply_wal_pragma() -> None:
    """启动时设置 WAL (持久化)."""
    with get_conn() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")