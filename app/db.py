"""PostgreSQL 连接 + schema 迁移入口.

设计:
- psycopg 3, 单连接池由服务层按请求短连接获取 (单家庭规模足够)
- schema 由 app/migrations 版本化管理 (schema_meta + 有序步骤)
- autocommit=True: 保留 service 层显式 BEGIN/COMMIT 的事务写法,
  读语句各自独立提交, 不需要额外包裹
- 行用 dict_row, 与原先 sqlite3.Row 的按名取值一致
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg.pq import TransactionStatus

from . import config
from .migrations.runner import migrate


def init_db(url: str | None = None) -> dict:
    """初始化数据库 (启动时调用一次).

    建库/升级统一走迁移器: 空库会建全套表; 后续版本按 schema_meta 补跑。
    """
    target = _resolve_url(url)
    report = migrate(target)

    with psycopg.connect(target, row_factory=dict_row) as conn:
        conn.execute("SELECT 1").fetchone()
    return report


def _resolve_url(url: str | None) -> str:
    """取连接串, 没配就给出能照着做的报错。

    不配 DATABASE_URL 时若直接把空串交给 psycopg, 报出来的是
    "connection to server at failed" 这类看不出原因的错。这里直接告诉
    家长去哪儿填 —— 密码只在 .env 里, 不在代码里, 所以漏配是迟早的事。
    """
    target = (url or config.DATABASE_URL).strip()
    if not target:
        raise RuntimeError(
            "未配置 DATABASE_URL: 复制 .env.example 为 .env 并填入数据库连接串"
            "(密码只写在 .env, 不入版本库)"
        )
    return target


def connect(url: str | None = None, *, autocommit: bool = True) -> psycopg.Connection:
    return psycopg.connect(
        _resolve_url(url), row_factory=dict_row, autocommit=autocommit
    )


@contextmanager
def get_conn() -> Iterator[psycopg.Connection]:
    """获取连接.

    - autocommit: 由 service 层决定事务边界 (BEGIN/COMMIT 仍照旧可用)
    - 异常退出时若事务未收尾, 主动 ROLLBACK, 避免连接带着半个事务回到连接池
    """
    conn = connect()
    try:
        yield conn
    except Exception:
        try:
            if conn.info.transaction_status != TransactionStatus.IDLE:
                conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()
