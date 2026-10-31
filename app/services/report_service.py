"""每周课后周报 (M6).

一份周报回答三个问题:
  1. 这周做完了没有 (完成情况 / 未收尾清单);
  2. 时间花在哪 (学科用时、分科占比、估时偏差);
  3. 哪里卡住了 (三问里的难/烦/不专注、实际远超预估的任务),
     以及训练线和整体负荷。

口径:
- 与看板同源: 必做不含选做, 已收尾 = done + skipped, 完成率分母用必做。
- 时间只算 done, 实耗为空按预估计, 与复盘页/日结的 COALESCE 一致;
  但"总用时"计入选做 (做了就是花了时间), 偏差表排除选做 (评估估时质量)。
- 报告只跟孩子自己的历史比, 不评价天赋、不比较队友; 政策 60 分钟
  只作为浅色参考, 不进结论。
- 1–2 年级 (settings.grade) 用 lite 模式: 建议与表扬各少一条, 更短。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from .. import config
from ..db import get_conn
from . import settings_service, week_plan_service
from .analytics_service import WEEKDAY_NAMES, classify_day
from .daily_wrap_service import grade_mode
from .tenant import default_child_id, default_family_id

# 状态中文名只用于周报的行内展示与 CSV, 状态本身仍是库里的枚举。
STATUS_LABELS = {"todo": "待完成", "doing": "进行中", "done": "完成", "skipped": "跳过"}
TASK_KIND_LABELS = {"subject": "学科", "training": "培训", "habit": "习惯"}

# 实际用时达到预估的这个倍数, 才算"卡住的慢任务" —— 1.5 倍以下
# 属于正常波动, 列出来只会让每周清单都一样长。
SLOW_RATIO = 1.5
SLOW_LIMIT = 3


def _today() -> date:
    return datetime.now(timezone.utc).astimezone().date()


def _week_bounds(anchor: date) -> tuple[date, date]:
    start = anchor - timedelta(days=anchor.weekday())
    return start, start + timedelta(days=6)


def _calendar() -> tuple[dict[str, str], dict[str, str]]:
    """合并校历 (与 analytics_service 同一份 config 数据)."""
    holidays: dict[str, str] = {}
    makeup: dict[str, str] = {}
    for table in config.SCHOOL_HOLIDAYS_BY_YEAR.values():
        holidays.update(table)
    for table in config.SCHOOL_MAKEUP_CLASS_DAYS_BY_YEAR.values():
        makeup.update(table)
    return holidays, makeup


def _iso(d) -> str:
    return d.isoformat() if hasattr(d, "isoformat") else str(d)


def _ordered_subjects(values: dict[str, int]) -> dict[str, int]:
    """固定 语文→数学→英语 顺序, 与看板/复盘的颜色映射一致."""
    return {s: values[s] for s in config.SUBJECTS if s in values}


def _fetch_assignments(conn, family_id: int, start: date, end: date) -> list:
    return conn.execute(
        """
        SELECT a.due_date, a.subject, a.task_kind, a.project_id,
               a.task_name, a.status, a.est_minutes, a.actual_minutes,
               a.is_optional, a.task_type, p.name AS project_name
        FROM assignments a
        LEFT JOIN projects p ON p.id = a.project_id
        WHERE a.family_id = %s AND a.due_date BETWEEN %s AND %s
        ORDER BY a.due_date, a.id
        """,
        (family_id, start, end),
    ).fetchall()


def _completion(rows: list) -> dict:
    total = len(rows)
    optional = sum(1 for r in rows if r["is_optional"])
    required = total - optional
    done = sum(1 for r in rows if not r["is_optional"] and r["status"] == "done")
    skipped = sum(1 for r in rows if not r["is_optional"] and r["status"] == "skipped")
    pending_items = [
        r for r in rows
        if not r["is_optional"] and r["status"] in ("todo", "doing")
    ]
    if required > 0:
        rate = min(100, round((done + skipped) * 100 / required))
    else:
        rate = 100 if rows else 0
    return {
        "total": total,
        "optional": optional,
        "required": required,
        "done": done,
        "skipped": skipped,
        "pending": len(pending_items),
        "settled": done + skipped,
        "rate": rate,
        "unfinished": [
            {
                "date": _iso(r["due_date"]),
                "weekday": WEEKDAY_NAMES[r["due_date"].weekday()],
                "task_kind": r["task_kind"],
                "subject": r["subject"],
                "project_name": r["project_name"],
                "task_name": r["task_name"],
                "status": r["status"],
            }
            for r in pending_items
        ],
    }


def _time_structure(rows: list, school_days: int) -> dict:
    by_subject: dict[str, int] = {}
    active: set[str] = set()
    total_minutes = 0
    for r in rows:
        if r["status"] != "done":
            continue
        if r["task_type"] == "observation":
            continue
        ds = _iso(r["due_date"])
        tasks = int(r["actual_minutes"] if r["actual_minutes"] is not None else r["est_minutes"]) or 0
        total_minutes += tasks
        active.add(ds)
        if r["task_kind"] == "subject" and r["subject"]:
            by_subject[r["subject"]] = by_subject.get(r["subject"], 0) + tasks

    ordered = _ordered_subjects(by_subject)
    denom = school_days or len(active) or 1
    share = {
        s: round(m * 100 / total_minutes, 1) if total_minutes else 0
        for s, m in ordered.items()
    }
    return {
        "total_minutes": total_minutes,
        "active_days": len(active),
        "school_days": school_days,
        "avg_daily_minutes": round(total_minutes / denom, 1),
        "by_subject": ordered,
        "subject_share": share,
        # 政策口径只做浅色参考, 明确写出来避免被当成家庭标准。
        "policy_reference_minutes": 60,
    }


def _deviation(rows: list) -> dict:
    samples: dict[tuple[str, str], list[tuple[int, int]]] = {}
    for r in rows:
        if r["status"] != "done" or r["is_optional"]:
            continue
        if r["task_type"] == "observation":
            continue
        est = int(r["est_minutes"] or 0)
        if est <= 0:
            continue
        actual = int(r["actual_minutes"] if r["actual_minutes"] is not None else est)
        key = (r["subject"] or "其他", r["task_type"] or "其他")
        samples.setdefault(key, []).append((est, actual))

    n = sum(len(v) for v in samples.values())
    if n < 5:
        return {
            "sample_size": n,
            "note": f"本周完成 {n} 条, 样本不足 5 条, 暂不生成估时偏差表",
            "by_type": [],
            "top_overestimate": [],
            "top_underestimate": [],
        }

    by_type: list[dict] = []
    for (subject, task_type), pairs in samples.items():
        k = len(pairs)
        avg_est = round(sum(e for e, _ in pairs) / k, 1)
        avg_act = round(sum(a for _, a in pairs) / k, 1)
        diff = round(avg_act - avg_est, 1)
        by_type.append({
            "subject": subject,
            "task_type": task_type,
            "sample_size": k,
            "avg_est_minutes": avg_est,
            "avg_actual_minutes": avg_act,
            "diff_minutes": diff,
            "diff_pct": round(diff / avg_est * 100, 1) if avg_est else 0.0,
        })

    over = sorted((x for x in by_type if x["diff_minutes"] < 0), key=lambda x: x["diff_minutes"])[:3]
    under = sorted((x for x in by_type if x["diff_minutes"] > 0), key=lambda x: -x["diff_minutes"])[:3]
    return {
        "sample_size": n,
        "note": None,
        "by_type": by_type,
        "top_overestimate": over,
        "top_underestimate": under,
    }


def _blockers(rows: list, reflections: list) -> dict:
    hard_days, bad_focus_days, bad_mood_days = [], [], []
    for r in reflections:
        ds = _iso(r["reflect_date"])
        item = {"date": ds, "weekday": WEEKDAY_NAMES[r["reflect_date"].weekday()]}
        if r["difficulty"] == "hard":
            hard_days.append(item)
        if r["focus"] == "bad":
            bad_focus_days.append(item)
        if r["mood"] == "bad":
            bad_mood_days.append(item)

    slow: list[dict] = []
    for r in rows:
        if r["status"] != "done" or r["is_optional"] or r["actual_minutes"] is None:
            continue
        est = int(r["est_minutes"] or 0)
        if est <= 0:
            continue
        ratio = r["actual_minutes"] / est
        if ratio < SLOW_RATIO:
            continue
        slow.append({
            "date": _iso(r["due_date"]),
            "weekday": WEEKDAY_NAMES[r["due_date"].weekday()],
            "subject": r["subject"],
            "project_name": r["project_name"],
            "task_name": r["task_name"],
            "est_minutes": est,
            "actual_minutes": int(r["actual_minutes"]),
            "ratio": round(ratio, 1),
        })
    slow.sort(key=lambda x: -x["ratio"])

    return {
        "hard_days": hard_days,
        "bad_focus_days": bad_focus_days,
        "bad_mood_days": bad_mood_days,
        "slow_tasks": slow[:SLOW_LIMIT],
    }


def _training(conn, family_id: int, start: date, end: date) -> dict:
    rows = conn.execute(
        """
        SELECT tl.log_date, tl.duration_minutes, tl.segment, tl.project_id,
               p.name AS project_name
        FROM training_logs tl
        LEFT JOIN projects p ON p.id = tl.project_id
        WHERE tl.family_id = %s AND tl.log_date BETWEEN %s AND %s
        ORDER BY tl.log_date, tl.id
        """,
        (family_id, start, end),
    ).fetchall()

    grouped: dict[int, dict] = {}
    for r in rows:
        key = r["project_id"] if r["project_id"] is not None else 0
        g = grouped.setdefault(key, {
            "project_id": r["project_id"],
            "project_name": r["project_name"],
            "logs": 0,
            "total_minutes": 0,
            "last_segment": None,
        })
        g["logs"] += 1
        g["total_minutes"] += int(r["duration_minutes"] or 0)
        # 结果集按 log_date,id 升序, 最后一条即"最近一次练到哪一段"。
        if r["segment"]:
            g["last_segment"] = r["segment"]

    by_project = []
    for g in grouped.values():
        logs = g["logs"]
        by_project.append({
            **g,
            "avg_minutes": round(g["total_minutes"] / logs, 1) if logs else 0,
        })
    by_project.sort(key=lambda x: -x["total_minutes"])

    return {
        "logs": len(rows),
        "total_minutes": sum(int(r["duration_minutes"] or 0) for r in rows),
        "by_project": by_project,
    }


def _load(anchor: date) -> dict:
    week = week_plan_service.get_week_plan(anchor)
    days = week["days"]
    fullest = max(
        (d for d in days if d["load_minutes"] > 0),
        key=lambda d: d["load_minutes"],
        default=None,
    )
    return {
        "limit_minutes": week["daily_load_limit"],
        "slot_minutes": week["totals"]["slot_minutes"],
        "load_minutes": week["totals"]["load_minutes"],
        "overloaded_days": week["totals"]["overloaded_days"],
        "conflict_days": week["totals"]["conflict_days"],
        "fullest_day": (
            {
                "date": fullest["date"],
                "weekday": fullest["label"],
                "load_minutes": fullest["load_minutes"],
            }
            if fullest else None
        ),
    }


def _previous_week(conn, family_id: int, start: date, end: date) -> dict:
    prev_start = start - timedelta(days=7)
    prev_end = end - timedelta(days=7)
    rows = _fetch_assignments(conn, family_id, prev_start, prev_end)
    prev = _completion(rows)
    minutes = 0
    active: set[str] = set()
    for r in rows:
        if r["status"] != "done" or r["task_type"] == "observation":
            continue
        minutes += int(r["actual_minutes"] if r["actual_minutes"] is not None else r["est_minutes"]) or 0
        active.add(_iso(r["due_date"]))
    return {"done": prev["done"], "minutes": minutes, "active_days": len(active)}


def _praise(completion: dict, time_stats: dict, training: dict, trend: dict, mode: str) -> list[str]:
    """只陈述事实, 不做天赋评价, 也不跟别人比."""
    out: list[str] = []
    if trend["done_delta"] > 0:
        out.append(f"比上周多完成了 {trend['done_delta']} 项")
    if time_stats["active_days"] >= 5:
        out.append(f"这周有 {time_stats['active_days']} 天完成了作业")
    if training["logs"] > 0:
        out.append(f"训练打卡 {training['logs']} 次, 累计 {training['total_minutes']} 分钟")
    if completion["required"] > 0 and completion["settled"] >= completion["required"]:
        out.append("本周必做全部收尾")
    if trend["minutes_delta"] < 0 and time_stats["total_minutes"] > 0:
        out.append(f"总用时比上周少了 {abs(trend['minutes_delta'])} 分钟")
    return out[:2 if mode == "lite" else 3]


def _suggestions(completion: dict, blockers: dict,
                 training: dict, load: dict, mode: str) -> list[str]:
    """规则建议, 最多 3 条 (1–2 年级 2 条). 先当下有事, 再下次能改."""
    out: list[str] = []

    if completion["total"] == 0:
        out.append("本周还没有作业记录, 先把老师通知登记进来, 周报才有内容")
    if completion["pending"]:
        names = "、".join(u["task_name"] for u in completion["unfinished"][:3])
        out.append(f"还有 {completion['pending']} 项没收尾 ({names}), 下周把最难的一项排在最前面")
    if blockers["bad_mood_days"]:
        out.append(f"有 {len(blockers['bad_mood_days'])} 天孩子情绪不好, 先聊两句再收尾, 睡前不批评")
    if blockers["slow_tasks"]:
        s = blockers["slow_tasks"][0]
        out.append(
            f"{s['task_name']} 实际 {s['actual_minutes']} 分钟, 是预估的 {s['ratio']} 倍, "
            f"下次直接按 {s['actual_minutes']} 分钟排"
        )
    if load["overloaded_days"]:
        out.append(f"有 {load['overloaded_days']} 天负荷超过 {load['limit_minutes']} 分钟, 考虑把一项挪到空一点的日子")
    if load["conflict_days"]:
        out.append(f"有 {load['conflict_days']} 天课表时段撞车, 先去「周计划」页调整")
    if blockers["hard_days"] and not blockers["bad_mood_days"]:
        out.append(f"有 {len(blockers['hard_days'])} 天任务偏难, 试着把最难的科目放在孩子状态最好的时段")
    if training["logs"] == 0 and not out:
        out.append("如果这周有训练项目, 练完顺手打个卡, 训练线才能看出进步")
    if not out:
        out.append("本周节奏平稳, 保持这个安排就好")
    return out[:2 if mode == "lite" else 3]


def _coach_note(training: dict) -> str | None:
    """给教练的一句话: 只陈述频次与最近进度, 不做效果判断."""
    if not training["logs"]:
        return None
    parts = [f"本周训练 {training['logs']} 次, 共 {training['total_minutes']} 分钟"]
    top = training["by_project"][0]
    if top["last_segment"]:
        parts.append(f"{top['project_name'] or '训练'}最近练到「{top['last_segment']}」")
    return "；".join(parts)


def build_weekly_report(anchor: date, today: date | None = None) -> dict:
    """anchor 所在自然周 (周一~周日) 的课后周报."""
    today = today or _today()
    start, end = _week_bounds(anchor)
    family_id = default_family_id()
    child_id = default_child_id()

    holidays, makeup = _calendar()
    school_days = sum(
        1 for i in range(7)
        if classify_day(start + timedelta(days=i), holidays, makeup)[0]
        in ("plain", "friday", "makeup")
    )

    with get_conn() as conn:
        rows = _fetch_assignments(conn, family_id, start, end)
        reflections = conn.execute(
            "SELECT reflect_date, difficulty, focus, mood, note FROM daily_reflections "
            "WHERE family_id = %s AND child_id = %s AND reflect_date BETWEEN %s AND %s "
            "ORDER BY reflect_date",
            (family_id, child_id, start, end),
        ).fetchall()
        training = _training(conn, family_id, start, end)
        prev = _previous_week(conn, family_id, start, end)

    completion = _completion(rows)
    time_stats = _time_structure(rows, school_days)
    deviation = _deviation(rows)
    blockers = _blockers(rows, reflections)
    load = _load(anchor)
    trend = {
        "done_delta": completion["done"] - prev["done"],
        "minutes_delta": time_stats["total_minutes"] - prev["minutes"],
        "active_days_delta": time_stats["active_days"] - prev["active_days"],
        "prev_done": prev["done"],
        "prev_minutes": prev["minutes"],
        "prev_active_days": prev["active_days"],
    }

    grade = settings_service.get_setting("grade")
    mode = grade_mode(grade)

    return {
        "week_start": start.isoformat(),
        "week_end": end.isoformat(),
        "today": today.isoformat(),
        "grade": grade,
        "mode": mode,
        "has_data": bool(rows),
        "completion": completion,
        "time": time_stats,
        "deviation": deviation,
        "blockers": blockers,
        "training": training,
        "load": load,
        "trend": trend,
        "praise": _praise(completion, time_stats, training, trend, mode),
        "coach_note": _coach_note(training),
        "suggestions": _suggestions(completion, blockers, training, load, mode),
    }
