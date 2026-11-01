"""PostgreSQL 连接 + schema 迁移入口.

设计:
- psycopg 3, 单连接池由服务层按请求短连接获取 (单家庭规模足够)
- schema 由 app/migrations 版本化管理 (schema_meta + 有序步骤)
- autocommit=True: 保留 service 层显式 BEGIN/COMMIT 的事务写法,
  读语句各自独立提交, 不需要额外包裹
- 行用 dict_row, 与原先 sqlite3.Row 的按名取值一致
"""
from __future__ import annotations

import logging
import socket
import time
from contextlib import contextmanager
from typing import Iterator
from urllib.parse import urlsplit

import psycopg
from psycopg.rows import dict_row
from psycopg.pq import TransactionStatus

from . import config
from .migrations.runner import migrate

logger = logging.getLogger("study.db")

# ====== PostgreSQL 连接与迁移入口 (M0 数据底座) ======
#
# 单机 SQLite 时代留下的约定在这里全部换成了 psycopg 3:
# 连接、schema 版本管理、事务边界各归其位, 服务层只管写业务 SQL。

# 探测结果: ready / not_listening / config_error
READY = "ready"
NOT_LISTENING = "not_listening"
CONFIG_ERROR = "config_error"


def _mask(url: str) -> str:
    """连接串脱敏: 日志和报错里不能出现明文密码."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<连接串格式异常>"
    if not parts.username:
        return url
    host = parts.hostname or ""
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{parts.username}:***@{host}{port}{parts.path}"


def probe_db(target: str, timeout: float = 2.0) -> tuple[str, str]:
    """探数据库就绪与否, 分两层.

    分两层是必要的: "容器还没起来" 和 "密码写错了" 表面都是连不上, 但
    处理方式完全相反 —— 前者等一等就好, 后者等再久也没用, 必须立刻
    让人看见。psycopg 3.3 不透传连接失败的 SQLSTATE (密码错与端口拒连
    都是 OperationalError), 所以只能先探 TCP 端口通不通来区分。

    返回 (状态, 详情); 状态为 READY 时详情为空串。
    """
    parts = urlsplit(target)
    host = parts.hostname or "127.0.0.1"
    port = parts.port or 5432

    # 第一层: TCP 端口通不通
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
    except OSError as exc:
        return NOT_LISTENING, f"{host}:{port} 连不上 ({exc.strerror or exc})"

    # 第二层: 端口通了, 那连不上就是配置问题, 不是"还没起来"
    try:
        with psycopg.connect(target, connect_timeout=max(3, int(timeout) + 1)):
            pass
    except Exception as exc:  # noqa: BLE001 - 任何失败都要原样带出去
        return CONFIG_ERROR, f"{host}:{port} 端口通, 但数据库连接失败: {exc}"
    return READY, ""


def db_startup_hint(target: str, detail: str = "") -> str:
    """数据库连不上时给"下一步做什么", 而不是一坨 traceback."""
    return (
        f"数据库未就绪: {_mask(target)}\n"
        f"  {detail}\n\n"
        "这个服务依赖 PostgreSQL, 按顺序排查:\n"
        "  1. 容器在不在    docker ps --filter name=study-pg\n"
        "  2. 端口发布了没  docker inspect -f '{{json .NetworkSettings.Ports}}' study-pg\n"
        "                   空 {} 或只有 {\"5432/tcp\":[]} = 端口没发布 (容器 Up 也会这样),\n"
        "                   跑 docker restart study-pg 再看一次\n"
        "  3. 端口通不通    nc -zv 127.0.0.1 5434\n"
        "  4. 连接串对不对  .env 的 DATABASE_URL (密码只写在 .env, 别写进代码)\n\n"
        "想让服务自己等数据库起来: python3 scripts/wait_for_db.py"
    )


def init_db(url: str | None = None) -> dict:
    """初始化数据库 (启动时调用一次).

    建库/升级统一走迁移器: 空库会建全套表; 后续版本按 schema_meta 补跑。

    数据库暂时连不上时有限次重试 (指数退避), 因为启动期撞上 Docker
    Desktop 刚开机、容器还没 ready 是常事, 直接崩掉会让守护进程
    (PM2) 在 2 秒内烧光重启预算并永久放弃 —— 详见 config.py 的说明。
    """
    target = _resolve_url(url)
    attempts = max(1, config.DB_INIT_ATTEMPTS)

    for i in range(attempts):
        state, detail = probe_db(target)
        if state == READY:
            break
        if state == CONFIG_ERROR:
            # 端口通但连不上 = 密码/库名/认证不对, 等也没用
            raise RuntimeError(db_startup_hint(target, detail)) from None
        if i == attempts - 1:
            raise RuntimeError(db_startup_hint(target, detail)) from None
        delay = config.DB_INIT_RETRY_DELAY * (2 ** i)
        logger.warning(
            "数据库还没起来 (%s), %.1fs 后重试 (%d/%d)", detail, delay, i + 1, attempts
        )
        time.sleep(delay)

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
