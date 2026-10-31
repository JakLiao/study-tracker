"""M3 日结: 晚间三问 + 每晚小结.

两条产品约束决定这里的实现:

1. **与看板同源.** summary 的完成数/用时/逾期全部取自 build_board_for 的
   同一份数据 (progress + 条目), 不另写一套 SQL。否则看板显示"5/6",
   小结却说"4/6", 家长两个都不信。weekly 报告将来也走这条口径。

2. **低年级两问版.** 1–2 年级的孩子对"专注"的自我报告不可靠, 界面只问
   难度与情绪; 后端按 settings.grade 返回 mode=lite, 前端据此少画一问。
   判定放在服务端而不是前端: 换设备/换浏览器都按家庭配置走。

三问的价值不在当晚, 而在周报归因: "周三数学多花 40 分钟, 那天难度=难、
情绪=烦" —— 这类"为什么慢"的解释, 只有固定档位才能聚合。
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any

from ..db import get_conn
from ..models import DailyReflectionSave, _as_date, _as_datetime
from . import assignment_service, settings_service
from .tenant import default_child_id, default_family_id


def local_today() -> date:
    """本地日期. 与看板 _local_today 同口径 (凌晨不退到前一天)."""
    return datetime.now(timezone.utc).astimezone().date()


def grade_mode(grade: str | None) -> str:
    """1–2 年级用两问表情版, 其余用三问."""
    match = re.search(r"\d+", grade or "")
    if match and int(match.group()) <= 2:
        return "lite"
    return "full"


# ====== 三问读写 ======

def _reflection_dict(row) -> dict[str, Any]:
    updated = _as_datetime(row["updated_at"])
    return {
        "date": _as_date(row["reflect_date"]).isoformat(),
        "difficulty": row["difficulty"],
        "focus": row["focus"],
        "mood": row["mood"],
        "note": row["note"],
        "updated_at": updated.isoformat() if updated else None,
    }


def _fetch_reflection(conn, family_id: int, child_id: int, d: date) -> dict | None:
    row = conn.execute(
        "SELECT * FROM daily_reflections "
        "WHERE family_id = %s AND child_id = %s AND reflect_date = %s",
        (family_id, child_id, d),
    ).fetchone()
    return _reflection_dict(row) if row else None


def fetch_reflection(d: date) -> dict | None:
    """只读某一天的三问; 没有记录返回 None (不是 404)."""
    family_id = default_family_id()
    child_id = default_child_id()
    with get_conn() as conn:
        return _fetch_reflection(conn, family_id, child_id, d)


def save_reflection(req: DailyReflectionSave, today: date | None = None) -> dict:
    """写入/覆盖当晚三问, 返回与 GET 相同结构的小结.

    upsert 而不是 insert: 家长晚上记错档位会再点一次, 一天只该有一条。
    created_at 保留首次记录时间, updated_at 记录最后一次修改。
    """
    family_id = default_family_id()
    child_id = default_child_id()
    now = datetime.now(timezone.utc)
    with get_conn() as conn:
        conn.execute("BEGIN")
        try:
            conn.execute(
                """
                INSERT INTO daily_reflections
                    (family_id, child_id, reflect_date, difficulty, focus, mood, note,
                     created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (family_id, child_id, reflect_date) DO UPDATE SET
                    difficulty = excluded.difficulty,
                    focus = excluded.focus,
                    mood = excluded.mood,
                    note = excluded.note,
                    updated_at = excluded.updated_at
                """,
                (
                    family_id,
                    child_id,
                    req.date,
                    req.difficulty,
                    req.focus,
                    req.mood,
                    req.note,
                    now,
                    now,
                ),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return get_daily_wrap(req.date, today)


# ====== 小结 ======

def _all_items(board: dict) -> list:
    """看板把条目分成 学科桶 / 培训习惯桶 / 选做, 这里拼回一维列表。

    三块互不重叠(选做在 build_board_for 里走单独分支), 不会重复计数。
    """
    items = []
    for lst in board["by_subject"].values():
        items.extend(lst)
    for lst in board["by_kind"].values():
        items.extend(lst)
    items.extend(board["optional_items"])
    return items


def _headline(pending_n: int, required: int, overdue_n: int, has_data: bool) -> str:
    if not has_data:
        return "今天没有作业记录"
    if pending_n:
        return f"今天还剩 {pending_n} 项没做完"
    if required == 0:
        return "今天只有选做，已经收尾"
    if overdue_n:
        return f"今天全部收尾，另有 {overdue_n} 项逾期"
    return "今天全部收尾"


def _suggestions(summary: dict, reflection: dict | None) -> list[str]:
    """规则建议, 最多 3 条.

    顺序 = 先处理"当下有事"的, 再处理"下次能改"的, 最后才是常规鼓励。
    选大于 3 条时家长一条都不会做, 所以截断。
    """
    out: list[str] = []
    if not summary["has_data"]:
        out.append("今天没有作业记录，记得把老师通知登记进来，否则明晚的小结还是空的")
    if summary["overdue_count"]:
        out.append(
            f"有 {summary['overdue_count']} 项已过截止时间，今晚和孩子定一下：顺延到明天，还是直接放弃"
        )
    if summary["pending_count"]:
        names = "、".join(summary["pending_names"])
        out.append(f"还有 {summary['pending_count']} 项没收尾（{names}），明早先确认哪一项要补")
    if reflection:
        # 情绪排在难度/专注之前: 孩子状态最直接, 也最该在当晚被回应;
        # 难度与专注是"下次怎么改", 可以放到下一条。
        if reflection["mood"] == "bad":
            out.append("孩子情绪不太好：先聊两句再收尾，睡前不批评")
        if reflection["difficulty"] == "hard":
            out.append("今天任务偏难：明天把最难的一科排在最前面，做完再休息")
        if reflection["focus"] == "bad":
            out.append("专注度偏低：试试 25 分钟一段、中间休息 5 分钟，比硬撑一小时有效")
    # "实际超过预估"只在全部收尾时才有意义: 没做完时 est_total 必然
    # 大于 actual_total, 提前触发只会天天报同一句。
    over = summary["actual_total"] - summary["est_total"]
    if summary["pending_count"] == 0 and over >= 10 and summary["actual_total"] > summary["est_total"] * 1.2:
        out.append(f"实际比预估多 {over} 分钟：把常超时那项的估时调高些，下次排计划更准")
    if (
        summary["has_data"]
        and summary["required"] > 0
        and summary["settled"] >= summary["required"]
        and summary["overdue_count"] == 0
    ):
        out.append("今天全部收尾，给孩子一句具体的表扬（说出他做对了什么）")
    if not out:
        out.append("今天节奏平稳，保持这个安排就好")
    if len(out) < 3 and reflection is None:
        out.append("花 30 秒记一下今晚三问，周末就能看出孩子慢在哪")
    return out[:3]


def get_daily_wrap(d: date, today: date | None = None) -> dict:
    """某一天的小结: 汇总 + 三问 + 建议."""
    today = today or local_today()
    board = assignment_service.build_board_for(d, today)
    progress = board["progress"]
    items = _all_items(board)

    pending = [i for i in items if i.status in ("todo", "doing") and not i.is_optional]
    # 当日到点的逾期在条目里(is_overdue), 往期的在 overdue_items 里;
    # 两者不重叠(看板只列"今天之前"的往期), 相加才是家长要处理的全部。
    inline_overdue = [i for i in items if i.is_overdue]
    overdue_count = len(inline_overdue) + len(board["overdue_items"])

    family_id = default_family_id()
    child_id = default_child_id()
    with get_conn() as conn:
        reflection = _fetch_reflection(conn, family_id, child_id, d)

    summary = {
        "done": progress["done"],
        "required": progress["required"],
        "total": progress["total"],
        "settled": progress["settled"],
        "skipped": progress["skipped"],
        "optional": progress["optional"],
        "optional_done": progress["optional_done"],
        "pct": progress["pct"],
        "est_total": progress["est_total"],
        "actual_total": progress["actual_total"],
        "pending_count": len(pending),
        "pending_names": [i.task_name for i in pending[:3]],
        "overdue_count": overdue_count,
        "has_data": board["has_data"],
    }

    grade = settings_service.get_setting("grade")
    return {
        "date": d.isoformat(),
        "label": board["label"],
        "is_today": d == today,
        "mode": grade_mode(grade),
        "grade": grade,
        "headline": _headline(
            len(pending), progress["required"], overdue_count, board["has_data"]
        ),
        "summary": summary,
        "reflection": reflection,
        "suggestions": _suggestions(summary, reflection),
    }
