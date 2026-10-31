"""一次性导入: 旧 SQLite (data/study.db) -> PostgreSQL.

用法:
    env -u LD_LIBRARY_PATH python3 scripts/import_sqlite_to_pg.py           # 空库才导入
    env -u LD_LIBRARY_PATH python3 scripts/import_sqlite_to_pg.py --force   # 先清空再导入

保留原 id (parsed_batches / assignments / completion_logs), 导入后把序列推到 max(id),
避免后续 INSERT 撞主键。全部在单个事务里完成, 任一行失败整批回滚。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402
from app.db import init_db  # noqa: E402
from app.services.tenant import ensure_default_family_child  # noqa: E402

SQLITE_PATH = ROOT / "data" / "study.db"

TABLES = ("families", "children", "projects", "assignments", "parsed_batches", "completion_logs", "settings")


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _d(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


def _sqlite_rows(conn: sqlite3.Connection, table: str) -> list[dict]:
    cur = conn.execute(f"SELECT * FROM {table}")
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def main() -> None:
    parser = argparse.ArgumentParser(description="SQLite -> PostgreSQL 一次性导入")
    parser.add_argument("--sqlite", default=str(SQLITE_PATH))
    parser.add_argument("--url", default=None, help="默认 config.DATABASE_URL")
    parser.add_argument("--force", action="store_true", help="目标库已有数据时先清空")
    args = parser.parse_args()

    sqlite_path = Path(args.sqlite)
    if not sqlite_path.exists():
        raise SystemExit(f"找不到 SQLite 文件: {sqlite_path}")

    url = args.url or config.DATABASE_URL
    init_db(url)  # 确保 PG schema 已建好

    src = sqlite3.connect(str(sqlite_path))
    src.row_factory = sqlite3.Row

    assignments = _sqlite_rows(src, "assignments")
    batches = _sqlite_rows(src, "parsed_batches")
    logs = _sqlite_rows(src, "completion_logs")
    settings = _sqlite_rows(src, "settings")
    src.close()

    with psycopg.connect(url, row_factory=dict_row) as pg:
        pg.execute("BEGIN")
        try:
            existing = pg.execute("SELECT COUNT(*) AS n FROM assignments").fetchone()["n"]
            if existing and not args.force:
                raise SystemExit(
                    f"目标库已有 {existing} 条 assignments, 拒绝重复导入 (要先清空请加 --force)"
                )
            if args.force:
                # 只清业务表, 不动 schema_meta
                pg.execute(
                    "TRUNCATE " + ", ".join(TABLES) + " RESTART IDENTITY CASCADE"
                )

            family_id, child_id = ensure_default_family_child(conn=pg)

            # 1) 批次 (assignments.batch_id 依赖它, 先插)
            for b in batches:
                pg.execute(
                    """
                    INSERT INTO parsed_batches
                        (id, raw_text, raw_text_hash, parsed_at, ai_model_version, result_count, status, note)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        b["id"],
                        b["raw_text"],
                        b["raw_text_hash"],
                        _dt(b["parsed_at"]),
                        b["ai_model_version"],
                        b["result_count"],
                        b["status"],
                        b["note"],
                    ),
                )

            # 2) 作业 (全部归到默认家庭/孩子, task_kind=subject)
            for a in assignments:
                pg.execute(
                    """
                    INSERT INTO assignments
                        (id, family_id, child_id, project_id, subject, task_name, due_date,
                         deadline_time, is_optional, est_minutes, actual_minutes, status,
                         task_type, task_kind, source_span, confidence, batch_id,
                         created_at, updated_at)
                    VALUES (%s, %s, %s, NULL, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, 'subject', %s, %s, %s, %s, %s)
                    """,
                    (
                        a["id"],
                        family_id,
                        child_id,
                        a["subject"],
                        a["task_name"],
                        _d(a["due_date"]),
                        _dt(a["deadline_time"]),
                        bool(a["is_optional"]),
                        a["est_minutes"],
                        a["actual_minutes"],
                        a["status"],
                        a["task_type"],
                        a["source_span"],
                        a["confidence"],
                        a["batch_id"],
                        _dt(a["created_at"]),
                        _dt(a["updated_at"]),
                    ),
                )

            # 3) 流水
            for log in logs:
                pg.execute(
                    """
                    INSERT INTO completion_logs
                        (id, assignment_id, action, from_value, to_value, at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        log["id"],
                        log["assignment_id"],
                        log["action"],
                        log["from_value"],
                        log["to_value"],
                        _dt(log["at"]),
                    ),
                )

            # 4) 设置 (覆盖默认种子值, 以旧库为准)
            for s in settings:
                pg.execute(
                    """
                    INSERT INTO settings (key, value, updated_at) VALUES (%s, %s, %s)
                    ON CONFLICT (key) DO UPDATE
                        SET value = excluded.value, updated_at = excluded.updated_at
                    """,
                    (s["key"], s["value"], _dt(s["updated_at"])),
                )

            # 5) 序列推到 max(id)
            for table in ("parsed_batches", "assignments", "completion_logs"):
                seq = pg.execute(
                    "SELECT pg_get_serial_sequence(%s, 'id') AS seq", (table,)
                ).fetchone()["seq"]
                pg.execute(
                    "SELECT setval(%s, COALESCE((SELECT MAX(id) FROM " + table + "), 1))",
                    (seq,),
                )

            # 6) 守恒校验
            checks = {
                "assignments": len(assignments),
                "parsed_batches": len(batches),
                "completion_logs": len(logs),
                "settings": len(settings),
            }
            for table, expected in checks.items():
                actual = pg.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
                if actual != expected:
                    raise RuntimeError(f"{table} 数量不守恒: 源 {expected}, 目标 {actual}")

            pg.execute("COMMIT")
        except SystemExit:
            pg.execute("ROLLBACK")
            raise
        except Exception:
            pg.execute("ROLLBACK")
            raise

    print(
        "导入完成: assignments={} parsed_batches={} completion_logs={} settings={}".format(
            len(assignments), len(batches), len(logs), len(settings)
        )
    )


if __name__ == "__main__":
    main()
