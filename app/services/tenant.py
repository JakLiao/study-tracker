"""家庭/孩子上下文 (单家庭 MVP).

即使当前只有一个家庭, 所有业务查询也统一按 family_id 收口 ——
将来接入账号与云端时, SQL 不用回头再改一遍。

约定:
- 默认家庭/孩子由 ensure_default_family_child 幂等创建;
- 取当前上下文的函数每次只读一条 SELECT, 不缓存, 避免测试换库时拿到旧 id。
"""
from __future__ import annotations

from datetime import datetime, timezone

from .. import config

DEFAULT_FAMILY_NAME = "我的家庭"
DEFAULT_CHILD_NAME = "孩子"


def ensure_default_family_child(conn=None) -> tuple[int, int]:
    """确保至少存在一个家庭和一个孩子, 返回 (family_id, child_id).

    幂等: 已有家庭/孩子时直接复用 id, 不重复插入。
    传入 conn 时沿用它的事务 (迁移器就是这么用的); 不传则自己开连接。
    """
    if conn is not None:
        return _ensure(conn)

    from ..db import get_conn

    with get_conn() as own:
        own.execute("BEGIN")
        try:
            ids = _ensure(own)
            own.execute("COMMIT")
            return ids
        except Exception:
            own.execute("ROLLBACK")
            raise


def _ensure(conn) -> tuple[int, int]:
    now = datetime.now(timezone.utc)

    row = conn.execute("SELECT id FROM families ORDER BY id LIMIT 1").fetchone()
    if row:
        family_id = int(row["id"])
    else:
        cur = conn.execute(
            "INSERT INTO families (name, created_at, updated_at) VALUES (%s, %s, %s) RETURNING id",
            (DEFAULT_FAMILY_NAME, now, now),
        )
        family_id = int(cur.fetchone()["id"])

    row = conn.execute(
        "SELECT id FROM children WHERE family_id = %s ORDER BY id LIMIT 1",
        (family_id,),
    ).fetchone()
    if row:
        child_id = int(row["id"])
    else:
        cur = conn.execute(
            "INSERT INTO children (family_id, name, grade, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (family_id, DEFAULT_CHILD_NAME, config.DEFAULT_GRADE, now, now),
        )
        child_id = int(cur.fetchone()["id"])

    return family_id, child_id


def _read_default() -> tuple[int, int] | None:
    from ..db import get_conn

    with get_conn() as conn:
        family = conn.execute("SELECT id FROM families ORDER BY id LIMIT 1").fetchone()
        if not family:
            return None
        family_id = int(family["id"])
        child = conn.execute(
            "SELECT id FROM children WHERE family_id = %s ORDER BY id LIMIT 1",
            (family_id,),
        ).fetchone()
    if not child:
        return None
    return family_id, int(child["id"])


def default_family_id() -> int:
    """读路径只做一次 SELECT; 缺数据时才走 ensure (写)."""
    ids = _read_default()
    return ids[0] if ids else ensure_default_family_child()[0]


def default_child_id() -> int:
    ids = _read_default()
    return ids[1] if ids else ensure_default_family_child()[1]
