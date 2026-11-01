#!/usr/bin/env python3
"""等 PostgreSQL 就绪 —— 启动前的就绪探测 (给 pm2-start.sh / run.sh 用).

为什么要它, 而不是"崩了让 PM2 重启"
------------------------------------
数据库没起来时, 连接被拒是**瞬时**的, 应用进程 1 秒内就死。PM2 默认
max_restarts=15 / restart_delay=0, 15 次重试 2 秒内烧光, 之后 PM2 把
进程标成 errored 并**永久不再重试** —— 实测根因修好后 15 秒内一次都没
恢复 (容器 08:53 就绪, PM2 到 08:55 仍是 errored/pid=0)。

而 PM2 的 min_uptime 决定这次重启算不算"成功": 进程活过 min_uptime 就
计数清零。所以**活着等**永远不会触发放弃, **崩了等**会。

本脚本就是让服务"活着等": 数据库通了才放行 uvicorn。

探测分两层 (见 app/db.py:probe_db), 因为两种失败处理方式相反:
  1. TCP 端口不通 (容器没起 / 端口没发布) -> 值得等
  2. 端口通但连不上 (密码错 / 库名错)     -> 等也没用, 立刻报错

用法:
    python3 scripts/wait_for_db.py [--wait-seconds 120]

退出码:
    0  数据库已就绪
    1  超时仍未就绪
    2  配置不对 (密码/库名), 等也没用
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402
from app.db import (  # noqa: E402
    CONFIG_ERROR,
    READY,
    db_startup_hint,
    probe_db,
)


def main() -> int:
    ap = argparse.ArgumentParser(description="等 PostgreSQL 就绪")
    ap.add_argument(
        "--wait-seconds",
        type=int,
        default=config.DB_WAIT_SECONDS,
        help="最长等待秒数 (默认取 DB_WAIT_SECONDS, 默认 120)",
    )
    ap.add_argument("--interval", type=float, default=2.0, help="重试间隔秒数")
    ap.add_argument(
        "--quiet", action="store_true", help="只打印结果, 不打印每次重试的进度"
    )
    args = ap.parse_args()

    target = config.DATABASE_URL
    if not target:
        print(
            "[FAIL] 没配置 DATABASE_URL。复制 .env.example 为 .env 并填入连接串"
            "(密码只写在 .env, 不进版本库)",
            file=sys.stderr,
        )
        return 2

    deadline = time.monotonic() + max(0, args.wait_seconds)
    last_log = 0.0
    detail = ""

    while True:
        state, detail = probe_db(target)
        now = time.monotonic()
        if state == READY:
            print(f"[OK] 数据库已就绪, 放行启动 ({target.split('@')[-1]})")
            return 0
        if state == CONFIG_ERROR:
            print(db_startup_hint(target, detail), file=sys.stderr)
            return 2
        if not args.quiet and now - last_log >= 5:
            left = max(0, int(deadline - now))
            print(
                f"[WAIT] 数据库还没起来 ({detail}), {left}s 后放弃 ...",
                file=sys.stderr,
            )
            last_log = now
        if now >= deadline:
            print(db_startup_hint(target, detail), file=sys.stderr)
            return 1
        time.sleep(min(args.interval, max(0.0, deadline - now)))


if __name__ == "__main__":
    sys.exit(main())
