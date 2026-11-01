"""孩子端星星罐 / 勋章 / 家长 PIN (双角色改造 · 后端).

三条设计约束, 后面所有实现都从它们推出来:

1. **发星幂等落在 schema 上.** 同一条作业只能发一次星, 靠 kid_star_ledger
   上的 partial unique index (assignment_id IS NOT NULL) 兜底 —— 应用层
   "先查后插"在并发下必然漏 (两个请求同时看到"还没发过"), 唯一索引才是
   唯一的裁判。撤销完成时按 assignment_id **删掉**那一行, 而不是补一行
   负数: 负数行会永久留在账本里, 孩子端看到"完成作业 -1 星"会以为自己
   的星星被偷了。

2. **星星写入必须跟作业状态同事务.** assignment_service.update_status
   已经有 BEGIN/COMMIT, 所以这里所有写账本的函数都接受 conn 参数, 在那个
   事务里执行 —— 自己另开连接会跟持锁的事务互相等待 (死锁)。

3. **账本 reason 存 code, 自由文本放 note.** earn_task / bonus_all_done /
   bonus_manual / spend —— 前端按 code 映射成中文, 混进自由文本就没法
   映射了。家长自己写的说明 ("今天主动复习") 落在 note 列, 两件事分开存:
   code 供程序判断, note 供孩子和爸爸看。
   (2026-11-01 补: 最初 note 无处安放, 只能丢弃 —— 家长填了说明却看不见,
   等于接口收下了参数又扔掉。现由迁移 v6 补 note 列。)

口径说明 (与规范 §5.2 对齐):
- balance        = 账本所有行 stars 之和 (可以为负)
- today_earned   = 当天"获得"星星之和 (只算正数行, 手动扣除不冲减)
- jar.stars      = 当天获得数, cap 固定 20
- week.stars     = 本周(周一起算)获得数; days_active = 本周有获得的天数;
                   tasks_done = 本周 status='done' 的作业数
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterator

import psycopg

from ..db import get_conn
from ..errors import AppError, InsufficientStarsError, PinVerifyFailedError
from ..models import _as_date, _as_datetime
from .tenant import default_child_id, default_family_id


# ====== 常量 ======
JAR_CAP = 20              # 星星罐容量 (规范 §4.1: 20 颗封顶)
LEDGER_LIMIT = 30         # 账本最近 N 条
TASK_STARS = 1            # 完成一条作业 +1 星
ALL_DONE_BONUS = 3        # 当天全部必做完成 +3 星
STREAK_LOOKBACK_DAYS = 400  # 算连续天数时往回看的窗口

PBKDF2_ITERATIONS = 120_000
PIN_HASH_ALGO = "pbkdf2_sha256"
_PIN_RE = re.compile(r"^\d{4}$")


# ====== 业务错误 ======
# PinVerifyFailedError / InsufficientStarsError 定义在 app/errors.py,
# 与项目里其他业务异常放在一起 —— 错误码表散落在各 service 里, 迟早
# 会撞码 (4011 就这么撞过一次: 样本不足 与 密码错误 抢同一个码)。


class InvalidPinError(AppError):
    """PIN 格式不是 4 位数字."""

    code = "4003"
    default_message = "PIN 必须是 4 位数字"


# ====== 勋章目录 ======
#
# icon 是**内联 SVG path 的名字** (前端按名字取 symbol), 不是 emoji。
# 前端需要新增同名 symbol: star / flame / calendar / rocket / bee / shield
MEDAL_CATALOG: tuple[dict[str, str], ...] = (
    {
        "key": "first_star",
        "name": "启程星",
        "desc": "拿到第一颗星星",
        "icon": "star",
    },
    {
        "key": "streak_3",
        "name": "连续三天",
        "desc": "连续三天完成全部必做",
        "icon": "flame",
    },
    {
        "key": "streak_7",
        "name": "坚持一周",
        "desc": "连续七天完成全部必做",
        "icon": "flame",
    },
    {
        "key": "full_week",
        "name": "完美一周",
        "desc": "一周七天全部完成必做",
        "icon": "calendar",
    },
    {
        "key": "speedster",
        "name": "效率小达人",
        "desc": "一周完成五项以上, 实际用时比预估省两成",
        "icon": "rocket",
    },
    {
        "key": "focused",
        "name": "专心小蜜蜂",
        "desc": "一周三条以上小结专注度全为很专心",
        "icon": "bee",
    },
    {
        "key": "tag_master",
        "name": "错题克星",
        "desc": "一周错题多标签不超过一次, 完成率八成以上",
        "icon": "shield",
    },
)


# ====== 小工具 ======
def local_today() -> date:
    """本地日期 (与 daily_wrap_service.local_today 同口径)."""
    return datetime.now(timezone.utc).astimezone().date()


def _week_bounds(d: date) -> tuple[date, date]:
    """本周区间: 周一起算, 周日结束."""
    monday = d - timedelta(days=d.weekday())
    return monday, monday + timedelta(days=6)


@contextmanager
def _write_conn(conn: psycopg.Connection | None) -> Iterator[psycopg.Connection]:
    """传入 conn 就沿用它 (跟调用方同一个事务); 不传就自己开一个并包事务.

    作业状态与星星账本必须原子 —— 所以 assignment_service 里那些
    "已经开了事务" 的调用点一律把 conn 传进来。
    """
    if conn is not None:
        yield conn
        return
    with get_conn() as own:
        own.execute("BEGIN")
        try:
            yield own
            own.execute("COMMIT")
        except Exception:
            own.execute("ROLLBACK")
            raise


def _scope(family_id: int | None, child_id: int | None) -> tuple[int, int]:
    """没指定归属时用默认家庭/孩子 (单家庭 MVP)."""
    fam = default_family_id() if family_id is None else family_id
    kid = default_child_id() if child_id is None else child_id
    return fam, kid


# ====== 账本读写 ======
def _insert_ledger(
    conn,
    *,
    family_id: int,
    child_id: int,
    star_date: date,
    stars: int,
    reason: str,
    assignment_id: int | None = None,
    note: str | None = None,
) -> bool:
    """写一行账本. 返回 True = 真插了一行, False = 幂等跳过.

    assignment_id 非空时靠 partial unique index 幂等 (ON CONFLICT DO
    NOTHING); 为空 (手动奖励 / 全完成奖励) 时走普通 INSERT。

    note 是家长写给人看的一句话, 与机器码 reason 分开存 (迁移 v6 补的列)。
    """
    now = datetime.now(timezone.utc)
    note = (note or "").strip() or None
    if assignment_id is None:
        conn.execute(
            "INSERT INTO kid_star_ledger "
            "(family_id, child_id, star_date, stars, reason, assignment_id, note, created_at) "
            "VALUES (%s, %s, %s, %s, %s, NULL, %s, %s)",
            (family_id, child_id, star_date, stars, reason, note, now),
        )
        return True
    cur = conn.execute(
        "INSERT INTO kid_star_ledger "
        "(family_id, child_id, star_date, stars, reason, assignment_id, note, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (assignment_id) WHERE assignment_id IS NOT NULL DO NOTHING "
        "RETURNING id",
        (family_id, child_id, star_date, stars, reason, assignment_id, note, now),
    )
    return cur.fetchone() is not None


def _balance(conn, family_id: int, child_id: int) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(stars), 0) AS s FROM kid_star_ledger "
        "WHERE family_id = %s AND child_id = %s",
        (family_id, child_id),
    ).fetchone()
    return int(row["s"] or 0)


def _earned_sum(conn, family_id: int, child_id: int, start: date, end: date) -> int:
    """区间内"获得"的星星 (只算正数行)."""
    row = conn.execute(
        "SELECT COALESCE(SUM(stars), 0) AS s FROM kid_star_ledger "
        "WHERE family_id = %s AND child_id = %s AND stars > 0 "
        "AND star_date BETWEEN %s AND %s",
        (family_id, child_id, start, end),
    ).fetchone()
    return int(row["s"] or 0)


def _active_days(conn, family_id: int, child_id: int, start: date, end: date) -> int:
    """区间内有获得星星的天数."""
    row = conn.execute(
        "SELECT COUNT(DISTINCT star_date) AS n FROM kid_star_ledger "
        "WHERE family_id = %s AND child_id = %s AND stars > 0 "
        "AND star_date BETWEEN %s AND %s",
        (family_id, child_id, start, end),
    ).fetchone()
    return int(row["n"] or 0)


def _done_tasks(conn, family_id: int, start: date, end: date) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM assignments "
        "WHERE family_id = %s AND due_date BETWEEN %s AND %s AND status = 'done'",
        (family_id, start, end),
    ).fetchone()
    return int(row["n"] or 0)


def _ledger_rows(conn, family_id: int, child_id: int, limit: int = LEDGER_LIMIT) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, star_date, stars, reason, note, assignment_id, created_at "
        "FROM kid_star_ledger WHERE family_id = %s AND child_id = %s "
        "ORDER BY star_date DESC, id DESC LIMIT %s",
        (family_id, child_id, limit),
    ).fetchall()
    return [
        {
            "id": int(r["id"]),
            "date": _as_date(r["star_date"]).isoformat(),
            "stars": int(r["stars"]),
            "reason": r["reason"],
            "note": r.get("note"),
            "assignment_id": r["assignment_id"],
            "created_at": _as_datetime(r["created_at"]),
        }
        for r in rows
    ]


# ====== 作业进度 (必做口径) ======
def _required_progress(conn, family_id: int, d: date) -> tuple[int, int]:
    """当天 (必做总数, 必做已完成数)."""
    row = conn.execute(
        "SELECT COUNT(*) FILTER (WHERE NOT is_optional) AS req, "
        "       COUNT(*) FILTER (WHERE NOT is_optional AND status = 'done') AS req_done "
        "FROM assignments WHERE family_id = %s AND due_date = %s",
        (family_id, d),
    ).fetchone()
    return int(row["req"] or 0), int(row["req_done"] or 0)


def _all_required_done(conn, family_id: int, d: date) -> bool:
    """当天必做是否全部完成 (且至少有一条必做).

    选做不算数: 家长点过"选做"就不再是必须完成的事。
    """
    req, req_done = _required_progress(conn, family_id, d)
    return req > 0 and req == req_done


def _done_days_map(
    conn, family_id: int, start: date, end: date
) -> dict[date, tuple[int, int]]:
    """区间内每天的 (必做总数, 必做已完成数)."""
    rows = conn.execute(
        "SELECT due_date, "
        "       COUNT(*) FILTER (WHERE NOT is_optional) AS req, "
        "       COUNT(*) FILTER (WHERE NOT is_optional AND status = 'done') AS req_done "
        "FROM assignments WHERE family_id = %s AND due_date BETWEEN %s AND %s "
        "GROUP BY due_date",
        (family_id, start, end),
    ).fetchall()
    return {
        _as_date(r["due_date"]): (int(r["req"] or 0), int(r["req_done"] or 0))
        for r in rows
    }


def _streak_chain(done_map: dict[date, tuple[int, int]], d: date) -> int:
    """从 d 往回数连续"必做全部完成"的天数.

    等价于规范 §5.4 的"上一条有效日 + 1", 但直接从作业数据算, 因此
    **与事件到达顺序无关**: 先补昨天的作业再完成今天的, 或者反过来,
    结果都是 2 而不是 1。
    """
    count = 0
    cur = d
    while True:
        req, req_done = done_map.get(cur, (0, 0))
        if req <= 0 or req != req_done:
            break
        count += 1
        cur -= timedelta(days=1)
    return count


# ====== 星星账本对外接口 ======
def get_stars(d: date | None = None) -> dict[str, Any]:
    """GET /api/kid/stars 的完整结构 (规范 §5.2)."""
    target = d or local_today()
    family_id = default_family_id()
    child_id = default_child_id()
    monday, sunday = _week_bounds(target)

    with get_conn() as conn:
        balance = _balance(conn, family_id, child_id)
        today_earned = _earned_sum(conn, family_id, child_id, target, target)
        week_stars = _earned_sum(conn, family_id, child_id, monday, sunday)
        days_active = _active_days(conn, family_id, child_id, monday, sunday)
        tasks_done = _done_tasks(conn, family_id, monday, sunday)
        streak = _streak_info(conn, family_id, child_id, target)
        ledger = _ledger_rows(conn, family_id, child_id)

    return {
        "balance": balance,
        "today_earned": today_earned,
        "jar": {"date": target.isoformat(), "stars": today_earned, "cap": JAR_CAP},
        "streak": streak,
        "ledger": ledger,
        "week": {
            "stars": week_stars,
            "days_active": days_active,
            "tasks_done": tasks_done,
        },
    }


def _streak_info(conn, family_id: int, child_id: int, d: date) -> dict[str, Any]:
    """连续天数: current 取当天行, best 取历史最大值, last_date 取最后一行."""
    row = conn.execute(
        "SELECT current_streak FROM kid_streaks "
        "WHERE family_id = %s AND child_id = %s AND streak_date = %s",
        (family_id, child_id, d),
    ).fetchone()
    agg = conn.execute(
        "SELECT COALESCE(MAX(best_streak), 0) AS best, MAX(streak_date) AS last_date "
        "FROM kid_streaks WHERE family_id = %s AND child_id = %s",
        (family_id, child_id),
    ).fetchone()
    current = int(row["current_streak"]) if row else 0
    best = max(int(agg["best"] or 0), current)
    last_date = agg["last_date"]
    return {
        "current": current,
        "best": best,
        "last_date": _as_date(last_date).isoformat() if last_date else None,
    }


def grant_stars(
    stars: int,
    reason: str = "",
    *,
    conn=None,
    family_id: int | None = None,
    child_id: int | None = None,
) -> dict[str, Any]:
    """家长手动奖励 +N 星 (reason 记 bonus_manual, 说明写进 note)。

    reason 参数是家长写的说明, 落进 note 列给人看; 机器码 reason 仍是
    bonus_manual —— 前端按 code 映射中文, 混进自由文本就没法映射了。
    """
    fam, kid = _scope(family_id, child_id)
    target = local_today()
    with _write_conn(conn) as c:
        _insert_ledger(
            c,
            family_id=fam,
            child_id=kid,
            star_date=target,
            stars=abs(int(stars)),
            reason="bonus_manual",
            note=reason,
        )
        balance = _balance(c, fam, kid)
        today_earned = _earned_sum(c, fam, kid, target, target)
    return {"balance": balance, "today_earned": today_earned}


def redeem_stars(
    stars: int,
    reason: str = "",
    *,
    conn=None,
    family_id: int | None = None,
    child_id: int | None = None,
) -> dict[str, Any]:
    """家长手动扣除 (换奖励). reason 记 spend.

    不允许把余额扣成负数 —— 余额不够直接抛业务错误, 不做"先扣成负数
    再补回来"的假账。
    """
    fam, kid = _scope(family_id, child_id)
    target = local_today()
    amount = abs(int(stars))
    with _write_conn(conn) as c:
        balance = _balance(c, fam, kid)
        if balance < amount:
            raise InsufficientStarsError(
                f"星星不够: 现在只有 {balance} 颗, 要扣 {amount} 颗"
            )
        _insert_ledger(
            c,
            family_id=fam,
            child_id=kid,
            star_date=target,
            stars=-amount,
            reason="spend",
            note=reason,
        )
        balance = _balance(c, fam, kid)
    return {"balance": balance}


def revoke_assignment_stars(
    assignment_id: int,
    *,
    conn=None,
    family_id: int | None = None,
    child_id: int | None = None,
) -> bool:
    """撤销完成: 删掉这条作业的星 (不是补一行负数)."""
    fam, kid = _scope(family_id, child_id)
    with _write_conn(conn) as c:
        cur = c.execute(
            "DELETE FROM kid_star_ledger "
            "WHERE assignment_id = %s AND family_id = %s AND child_id = %s",
            (assignment_id, fam, kid),
        )
        return cur.rowcount > 0


def revoke_bonus_if_needed(
    d: date,
    *,
    conn=None,
    family_id: int | None = None,
    child_id: int | None = None,
) -> bool:
    """当天不再"必做全完成"时, 收回那笔 bonus_all_done.

    为什么必须回收: 全勤奖是**条件奖**, 条件不成立了还留着, 等于孩子白拿
    3 颗星。家长撤销一条完成 (或者把一条改成选做/不做), 当天就不算全勤了,
    这 3 颗星要跟着没 —— 和"撤销完成就删那颗星"是同一条原则。

    按同样的口径, 这里也是**删掉那一行**而不是补一行负数: 孩子端账本里
    不该出现"今天 -3 星"这种看不懂的记录。
    """
    fam, kid = _scope(family_id, child_id)
    with _write_conn(conn) as c:
        if _all_required_done(c, fam, d):
            return False
        cur = c.execute(
            "DELETE FROM kid_star_ledger "
            "WHERE family_id = %s AND child_id = %s AND star_date = %s "
            "AND reason = 'bonus_all_done'",
            (fam, kid, d),
        )
        return cur.rowcount > 0


def award_task_stars(
    assignment_id: int,
    *,
    conn=None,
    family_id: int | None = None,
    child_id: int | None = None,
) -> bool:
    """完成一条作业发 1 星 (reason=earn_task). 幂等.

    星星记在作业的**归属日** (due_date), 不是"点完成的当天" —— 否则
    补做昨天的作业会算到今天头上, 连续天数与本周统计都会失真。
    """
    fam, kid = _scope(family_id, child_id)
    with _write_conn(conn) as c:
        row = c.execute(
            "SELECT due_date FROM assignments WHERE id = %s AND family_id = %s",
            (assignment_id, fam),
        ).fetchone()
        if not row:
            return False
        return _insert_ledger(
            c,
            family_id=fam,
            child_id=kid,
            star_date=_as_date(row["due_date"]),
            stars=TASK_STARS,
            reason="earn_task",
            assignment_id=assignment_id,
        )


def bonus_all_done(
    d: date,
    *,
    conn=None,
    family_id: int | None = None,
    child_id: int | None = None,
) -> bool:
    """当天全部必做完成 +3 星 (reason=bonus_all_done). 每天只发一次."""
    fam, kid = _scope(family_id, child_id)
    with _write_conn(conn) as c:
        if not _all_required_done(c, fam, d):
            return False
        exists = c.execute(
            "SELECT 1 FROM kid_star_ledger "
            "WHERE family_id = %s AND child_id = %s AND star_date = %s "
            "AND reason = 'bonus_all_done' LIMIT 1",
            (fam, kid, d),
        ).fetchone()
        if exists:
            return False
        return _insert_ledger(
            c,
            family_id=fam,
            child_id=kid,
            star_date=d,
            stars=ALL_DONE_BONUS,
            reason="bonus_all_done",
        )


def _write_streak_row(
    conn,
    *,
    family_id: int,
    child_id: int,
    d: date,
    current: int,
    best: int,
    stars_earned: int,
    tasks_done: int,
    now: datetime,
) -> None:
    """写/更新某一天的连续天数行."""
    conn.execute(
        """
        INSERT INTO kid_streaks
            (family_id, child_id, streak_date, current_streak, best_streak,
             stars_earned, tasks_done, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (family_id, child_id, streak_date) DO UPDATE SET
            current_streak = excluded.current_streak,
            best_streak    = excluded.best_streak,
            stars_earned   = excluded.stars_earned,
            tasks_done     = excluded.tasks_done,
            updated_at     = excluded.updated_at
        """,
        (family_id, child_id, d, current, best, stars_earned, tasks_done, now),
    )


def _best_streak(conn, family_id: int, child_id: int) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(best_streak), 0) AS b FROM kid_streaks "
        "WHERE family_id = %s AND child_id = %s",
        (family_id, child_id),
    ).fetchone()
    return int(row["b"] or 0)


def update_streak(
    d: date,
    *,
    conn=None,
    family_id: int | None = None,
    child_id: int | None = None,
) -> dict[str, Any]:
    """更新 kid_streaks 的当天行.

    current_streak = 以当天为结尾的连续"必做全部完成"天数; 当天没全部
    完成就是 0。best_streak = max(历史最大, current) —— 只涨不跌,
    与规范 §5.4 一致。

    补做昨天之后, **今天那一行也要跟着重算**: 否则"连续天数"会取决于
    家长点"完成"的顺序 (先点今天的, 再补昨天的, 连续天数就断了)。
    所以这里会把 d 之后**已经存在**的行一并刷新 (只刷已存在的, 不给
    没动静的日子凭空造行)。
    """
    fam, kid = _scope(family_id, child_id)
    now = datetime.now(timezone.utc)
    today = local_today()
    with _write_conn(conn) as c:
        lo = min(d, today) - timedelta(days=STREAK_LOOKBACK_DAYS)
        hi = max(d, today)
        done_map = _done_days_map(c, fam, lo, hi)

        current = _streak_chain(done_map, d)
        best = max(_best_streak(c, fam, kid), current)
        stars_earned = _earned_sum(c, fam, kid, d, d)
        tasks_done = _done_tasks(c, fam, d, d)
        _write_streak_row(
            c,
            family_id=fam,
            child_id=kid,
            d=d,
            current=current,
            best=best,
            stars_earned=stars_earned,
            tasks_done=tasks_done,
            now=now,
        )

        # 往后刷新已经存在的行 (补做昨天 -> 今天的连续天数要跟着变)。
        # 先一次性把有行的日期捞出来, 再逐个重算 —— 比"从 d+1 扫到今天
        # 每天查一次"少很多次往返。
        later = c.execute(
            "SELECT streak_date FROM kid_streaks "
            "WHERE family_id = %s AND child_id = %s AND streak_date > %s AND streak_date <= %s",
            (fam, kid, d, today),
        ).fetchall()
        for row in later:
            day = _as_date(row["streak_date"])
            cur = _streak_chain(done_map, day)
            _write_streak_row(
                c,
                family_id=fam,
                child_id=kid,
                d=day,
                current=cur,
                best=max(_best_streak(c, fam, kid), cur),
                stars_earned=_earned_sum(c, fam, kid, day, day),
                tasks_done=_done_tasks(c, fam, day, day),
                now=now,
            )

    return {
        "date": d.isoformat(),
        "current_streak": current,
        "best_streak": best,
        "stars_earned": stars_earned,
        "tasks_done": tasks_done,
    }


def sync_status_stars(
    conn,
    *,
    assignment_id: int,
    family_id: int,
    child_id: int,
    due_date: date,
    old_status: str,
    new_status: str,
) -> dict[str, Any]:
    """作业状态变化后的星星联动 (在**同一个事务**里执行).

    规则见规范 §5.3:
      变成 done   -> +1 (earn_task); 当天必做全完成再 +3 (bonus_all_done)
      从 done 退回 -> 删掉那颗星; 重新算连续天数
    """
    result: dict[str, Any] = {"awarded": False, "revoked": False, "bonus": False}
    if new_status == "done":
        result["awarded"] = award_task_stars(
            assignment_id, conn=conn, family_id=family_id, child_id=child_id
        )
        result["bonus"] = bonus_all_done(
            due_date, conn=conn, family_id=family_id, child_id=child_id
        )
        result["streak"] = update_streak(
            due_date, conn=conn, family_id=family_id, child_id=child_id
        )
    elif old_status == "done":
        result["revoked"] = revoke_assignment_stars(
            assignment_id, conn=conn, family_id=family_id, child_id=child_id
        )
        # 全勤奖也是条件奖: 少了一条完成, 当天就不算"全部完成"了,
        # 那 3 颗星要收回来 (2026-11-01 补: 只回收单条星, 全勤奖会永久留下)。
        result["bonus_revoked"] = revoke_bonus_if_needed(
            due_date, conn=conn, family_id=family_id, child_id=child_id
        )
        result["streak"] = update_streak(
            due_date, conn=conn, family_id=family_id, child_id=child_id
        )
    return result


# ====== 勋章 ======
def _parse_tags(raw: str | None) -> list[str]:
    """daily_reflections.tags (逗号分隔 code) -> list."""
    if not raw:
        return []
    return [t.strip() for t in str(raw).split(",") if t.strip()]


def _evaluate_medals(conn, family_id: int, child_id: int, today: date) -> dict[str, tuple[date, dict[str, Any]]]:
    """按规范 §5.5 的 7 条规则评估, 返回 {medal_key: (获得日期, meta)}.

    规则全部**从数据算出来**, 不写死: 星星来自账本, 连续天数来自
    kid_streaks, 效率/完成率来自 assignments, 专注度与标签来自
    daily_reflections。
    """
    hits: dict[str, tuple[date, dict[str, Any]]] = {}
    monday, sunday = _week_bounds(today)

    # 1. first_star: 累计拿到第 1 颗星
    row = conn.execute(
        "SELECT MIN(star_date) AS d, COALESCE(SUM(stars), 0) AS s FROM kid_star_ledger "
        "WHERE family_id = %s AND child_id = %s AND stars > 0",
        (family_id, child_id),
    ).fetchone()
    if row["d"] is not None:
        hits["first_star"] = (
            _as_date(row["d"]),
            {"total_stars": int(row["s"] or 0)},
        )

    # 2/3. streak_3 / streak_7: 连续完成 N 天
    for key, need in (("streak_3", 3), ("streak_7", 7)):
        row = conn.execute(
            "SELECT MIN(streak_date) AS d FROM kid_streaks "
            "WHERE family_id = %s AND child_id = %s AND current_streak >= %s",
            (family_id, child_id, need),
        ).fetchone()
        if row["d"] is not None:
            hits[key] = (_as_date(row["d"]), {"streak": need})

    # 4. full_week: 某个自然周 (周一~周日) 七天必做全部完成
    start = monday - timedelta(weeks=8)
    done_map = _done_days_map(conn, family_id, start, sunday)
    for i in range(8, -1, -1):
        wk_start = monday - timedelta(weeks=i)
        days = [done_map.get(wk_start + timedelta(days=k), (0, 0)) for k in range(7)]
        if all(req > 0 and req == done for req, done in days):
            wk_end = wk_start + timedelta(days=6)
            hits["full_week"] = (
                wk_end,
                {"week_start": wk_start.isoformat(), "week_end": wk_end.isoformat()},
            )
            break

    # 5. speedster: 一周完成 >=5 项, 实际用时比预估少 >=20%
    row = conn.execute(
        "SELECT COUNT(*) AS n, "
        "       COALESCE(SUM(est_minutes), 0) AS est, "
        "       COALESCE(SUM(COALESCE(actual_minutes, est_minutes)), 0) AS act "
        "FROM assignments "
        "WHERE family_id = %s AND due_date BETWEEN %s AND %s AND status = 'done'",
        (family_id, monday, sunday),
    ).fetchone()
    done_n, est_total, act_total = int(row["n"] or 0), int(row["est"] or 0), int(row["act"] or 0)
    if done_n >= 5 and est_total > 0 and act_total <= est_total * 0.8:
        hits["speedster"] = (
            today,
            {
                "tasks": done_n,
                "est_minutes": est_total,
                "actual_minutes": act_total,
            },
        )

    # 本周小结 (focused / tag_master 共用)
    refl_rows = conn.execute(
        "SELECT reflect_date, focus, tags FROM daily_reflections "
        "WHERE family_id = %s AND child_id = %s AND reflect_date BETWEEN %s AND %s",
        (family_id, child_id, monday, sunday),
    ).fetchall()

    # 6. focused: 一周 >=3 条小结, 专注度全为 good
    if len(refl_rows) >= 3 and all(r["focus"] == "good" for r in refl_rows):
        last = max(_as_date(r["reflect_date"]) for r in refl_rows)
        hits["focused"] = (last, {"reflections": len(refl_rows)})

    # 7. tag_master: 一周 many_errors 标签 <=1 次, 且完成率 >=80%
    many_errors = sum(
        1 for r in refl_rows if "many_errors" in _parse_tags(r["tags"])
    )
    row = conn.execute(
        "SELECT COUNT(*) FILTER (WHERE NOT is_optional) AS req, "
        "       COUNT(*) FILTER (WHERE NOT is_optional AND status = 'done') AS req_done "
        "FROM assignments WHERE family_id = %s AND due_date BETWEEN %s AND %s",
        (family_id, monday, sunday),
    ).fetchone()
    req, req_done = int(row["req"] or 0), int(row["req_done"] or 0)
    rate = (req_done / req) if req else 0.0
    if many_errors <= 1 and req > 0 and rate >= 0.8:
        hits["tag_master"] = (
            today,
            {"many_errors": many_errors, "completion_rate": round(rate * 100)},
        )

    return hits


def get_medals() -> dict[str, Any]:
    """GET /api/kid/medals: {items, locked}.

    每次调用都重新评估规则, 命中的写进 kid_medals (UNIQUE 约束兜底幂等),
    已经拿到的勋章保留首次获得的日期。
    """
    family_id = default_family_id()
    child_id = default_child_id()
    today = local_today()

    with get_conn() as conn:
        conn.execute("BEGIN")
        try:
            hits = _evaluate_medals(conn, family_id, child_id, today)
            now = datetime.now(timezone.utc)
            for key, (earned_date, meta) in hits.items():
                conn.execute(
                    "INSERT INTO kid_medals "
                    "(family_id, child_id, medal_key, earned_date, meta, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (family_id, child_id, medal_key) DO NOTHING",
                    (
                        family_id,
                        child_id,
                        key,
                        earned_date,
                        json.dumps(meta, ensure_ascii=False) if meta else None,
                        now,
                    ),
                )
            rows = conn.execute(
                "SELECT medal_key, earned_date, meta FROM kid_medals "
                "WHERE family_id = %s AND child_id = %s",
                (family_id, child_id),
            ).fetchall()
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    earned = {r["medal_key"]: r for r in rows}
    items: list[dict[str, Any]] = []
    locked: list[dict[str, Any]] = []
    for m in MEDAL_CATALOG:
        key = m["key"]
        row = earned.get(key)
        if row:
            raw_meta = row["meta"]
            try:
                meta = json.loads(raw_meta) if raw_meta else None
            except (TypeError, ValueError):
                meta = None
            items.append(
                {
                    "medal_key": key,
                    "name": m["name"],
                    "desc": m["desc"],
                    "icon": m["icon"],
                    "earned_date": _as_date(row["earned_date"]).isoformat(),
                    "meta": meta,
                }
            )
        else:
            locked.append(
                {
                    "medal_key": key,
                    "name": m["name"],
                    "desc": m["desc"],
                    "icon": m["icon"],
                    "earned_date": None,
                    "meta": None,
                }
            )
    return {"items": items, "locked": locked}


# ====== 家长 PIN ======
def _hash_pin(pin: str) -> str:
    """PBKDF2-HMAC-SHA256, 16 字节随机盐, 12 万次迭代.

    存成 pbkdf2_sha256$120000$<salt_hex>$<hash_hex> —— 明文不出进程,
    也不落库。
    """
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"{PIN_HASH_ALGO}${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def _check_pin(pin: str, stored: str) -> bool:
    """比对 PIN. 用 hmac.compare_digest (定长比较, 不泄露时序信息)."""
    parts = str(stored).split("$")
    if len(parts) != 4:
        return False
    algo, iters_s, salt_hex, hash_hex = parts
    if algo != PIN_HASH_ALGO:
        return False
    try:
        iters = int(iters_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, iters)
    return hmac.compare_digest(digest, expected)


def set_pin(pin: str) -> dict[str, Any]:
    """设置/覆盖家长 PIN. 一个家庭一条 (parent_access.family_id UNIQUE)."""
    if not _PIN_RE.match(str(pin).strip()):
        raise InvalidPinError()
    pin = str(pin).strip()
    family_id = default_family_id()
    now = datetime.now(timezone.utc)
    with get_conn() as conn:
        conn.execute("BEGIN")
        try:
            conn.execute(
                "INSERT INTO parent_access (family_id, pin_hash, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (family_id) DO UPDATE SET "
                "pin_hash = excluded.pin_hash, updated_at = excluded.updated_at",
                (family_id, _hash_pin(pin), now, now),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return {"ok": True}


def verify_pin(pin: str) -> dict[str, Any]:
    """校验家长 PIN.

    还没设过 PIN -> 返回 {"ok": false} (不是错误: 首次使用要先设置);
    设过但输错 -> 抛 4011。
    """
    family_id = default_family_id()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT pin_hash FROM parent_access WHERE family_id = %s", (family_id,)
        ).fetchone()
    if not row:
        return {"ok": False}
    if not _check_pin(str(pin).strip(), row["pin_hash"]):
        raise PinVerifyFailedError()
    return {"ok": True}


def clear_pin() -> dict[str, Any]:
    """清除家长 PIN (孩子端不再需要门禁)."""
    family_id = default_family_id()
    with get_conn() as conn:
        conn.execute("DELETE FROM parent_access WHERE family_id = %s", (family_id,))
    return {"ok": True}


def pin_status() -> dict[str, Any]:
    """是否已设置 PIN."""
    family_id = default_family_id()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM parent_access WHERE family_id = %s", (family_id,)
        ).fetchone()
    return {"enabled": bool(row)}
