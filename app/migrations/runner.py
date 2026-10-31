"""轻量版本化迁移器 (PostgreSQL).

三条硬约束:
1. 迁移只执行一次: 已执行版本记在 schema_meta, 重跑直接跳过。
2. 整版原子: 每个 version 一个事务, 失败整版回滚 —— PG 的 DDL 是事务性的,
   不会留下半套 schema (SQLite 时代需要的文件备份因此不再必要)。
3. dry-run 只碰事务: 在真实库上跑完立即 ROLLBACK, 结构与数据都不留痕。

用法:
    python3 -m app.migrations.runner --dry-run
    python3 -m app.migrations.runner
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row

from .. import __version__, config
from .steps import MIGRATIONS, Migration

logger = logging.getLogger("study.migrations")

CURRENT_VERSION: int = max((m.version for m in MIGRATIONS), default=0)


class MigrationError(RuntimeError):
    """迁移失败."""


def _connect(url: str, *, autocommit: bool) -> psycopg.Connection:
    return psycopg.connect(url, row_factory=dict_row, autocommit=autocommit)


def _ensure_meta(conn: psycopg.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_meta (
            version     INTEGER PRIMARY KEY,
            name        TEXT NOT NULL,
            applied_at  TIMESTAMPTZ NOT NULL,
            app_version TEXT NOT NULL
        )
        """
    )


def _applied_versions(conn: psycopg.Connection) -> set[int]:
    cur = conn.execute(
        "SELECT to_regclass('schema_meta') IS NOT NULL AS exists"
    )
    exists = cur.fetchone()["exists"]
    if not exists:
        return set()
    cur = conn.execute("SELECT version FROM schema_meta")
    return {int(r["version"]) for r in cur.fetchall()}


def _assignment_count(conn: psycopg.Connection) -> int | None:
    cur = conn.execute("SELECT to_regclass('assignments') IS NOT NULL AS exists")
    if not cur.fetchone()["exists"]:
        return None
    cur = conn.execute("SELECT COUNT(*) AS n FROM assignments")
    return int(cur.fetchone()["n"])


def _apply(conn: psycopg.Connection, report: dict[str, Any]) -> None:
    """在调用方的事务里跑完所有待执行迁移 (dry-run 由调用方回滚)."""
    _ensure_meta(conn)
    done = _applied_versions(conn)
    report["version_before"] = max(done, default=0)
    pending = [m for m in MIGRATIONS if m.version not in done]
    report["pending"] = [f"{m.version:04d}_{m.name}" for m in pending]
    report["assignments_before"] = _assignment_count(conn)

    for migration in pending:
        try:
            _run_steps(conn, migration)
            conn.execute(
                "INSERT INTO schema_meta (version, name, applied_at, app_version) VALUES (%s, %s, %s, %s)",
                (
                    migration.version,
                    migration.name,
                    datetime.now(timezone.utc),
                    __version__,
                ),
            )
        except Exception:
            raise MigrationError(
                f"迁移 {migration.version:04d}_{migration.name} 失败"
            )
        report["applied"].append(f"{migration.version:04d}_{migration.name}")
        logger.info("applied migration %04d_%s", migration.version, migration.name)

    report["assignments_after"] = _assignment_count(conn)
    report["version_after"] = max(_applied_versions(conn), default=0)


def _run_steps(conn: psycopg.Connection, migration: Migration) -> None:
    for step in migration.steps:
        if callable(step):
            step(conn)
        else:
            conn.execute(step)


def _new_report(url: str, *, dry_run: bool) -> dict[str, Any]:
    return {
        "db": _safe_url(url),
        "dry_run": dry_run,
        "version_before": 0,
        "version_after": 0,
        "pending": [],
        "applied": [],
        "assignments_before": None,
        "assignments_after": None,
    }


def _safe_url(url: str) -> str:
    """报告里不出现密码."""
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(url)
    if parts.password:
        netloc = parts.netloc.replace(f":{parts.password}@", ":***@")
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    return url


def pending_migrations(url: str | None = None) -> list[str]:
    """只读查询: 列出尚未执行的迁移 (不会建 schema_meta)."""
    target = url or config.DATABASE_URL
    with psycopg.connect(target, row_factory=dict_row) as conn:
        done = _applied_versions(conn)
    return [f"{m.version:04d}_{m.name}" for m in MIGRATIONS if m.version not in done]


def migrate(url: str | None = None, dry_run: bool = False) -> dict[str, Any]:
    """执行迁移.

    Returns:
        dict 报告: db / dry_run / pending / applied /
        version_before / version_after / assignments_before / assignments_after
    """
    target = url or config.DATABASE_URL
    report = _new_report(target, dry_run=dry_run)

    conn = _connect(target, autocommit=False)
    try:
        _apply(conn, report)
        if dry_run:
            conn.rollback()
            logger.info("dry-run: pending=%s (已回滚, 未改动)", report["pending"])
        else:
            conn.commit()
    except Exception as e:
        conn.rollback()
        if isinstance(e, MigrationError):
            raise
        raise MigrationError(f"迁移失败: {e}") from e
    finally:
        conn.close()

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="study-tracker schema 迁移 (PostgreSQL)")
    parser.add_argument("--dry-run", action="store_true", help="跑完立即回滚, 不改库")
    parser.add_argument("--url", default=None, help="数据库连接串 (默认 config.DATABASE_URL)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    report = migrate(args.url, dry_run=args.dry_run)
    logger.info("migration report: %s", report)


if __name__ == "__main__":
    main()
