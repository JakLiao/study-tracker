"""周复盘偏差分析."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from ..db import get_conn
from ..errors import SampleTooSmallError


def _week_range(week: str, today: date) -> tuple[date, date]:
    """周一为一周开始."""
    monday = today - timedelta(days=today.weekday())
    if week == "current":
        return monday, monday + timedelta(days=6)
    if week == "last":
        last_mon = monday - timedelta(days=7)
        return last_mon, last_mon + timedelta(days=6)
    # 默认 current
    return monday, monday + timedelta(days=6)


def _empty_result(week: str, today: date, sample_size: int = 0, note: str | None = None):
    start, end = _week_range(week, today)
    return {
        "week": week,
        "week_start": start.isoformat(),
        "week_end": end.isoformat(),
        "sample_size": sample_size,
        "note": note,
        "by_type": [],
        "top_overestimate": [],
        "top_underestimate": [],
    }


def build_weekly_analytics(week: str, today: date) -> dict:
    """周维度偏差: 预估 vs 实际, 按 科目 x 任务类型 聚合.

    业务规则:
    - 只统计 status=done 且 actual_minutes IS NOT NULL 且 not is_optional 的记录
    - sample_size < 5 -> 抛 SampleTooSmallError (前端展示"样本不足")
    - sample_size >= 5 但没有可执行任务的, 返回 note 提示
    """
    start, end = _week_range(week, today)

    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT subject, task_type, est_minutes, actual_minutes
            FROM assignments
            WHERE due_date BETWEEN ? AND ?
              AND status = 'done'
              AND actual_minutes IS NOT NULL
              AND is_optional = 0
              AND task_type != 'observation'
            """,
            (start.isoformat(), end.isoformat()),
        ).fetchall()

    sample_size = len(rows)
    if sample_size < 5:
        raise SampleTooSmallError(
            f"本周完成数 {sample_size} < 5, 样本不足, 不生成偏差表"
        )

    if not rows:
        return _empty_result(week, today, sample_size=0, note="本周有完成但无实际耗时数据")

    # 按 (subject, task_type) 聚合
    agg: dict[tuple[str, str], list[tuple[int, int]]] = defaultdict(list)
    for r in rows:
        subj = r["subject"] or "其他"
        ttype = r["task_type"] or "其他"
        agg[(subj, ttype)].append((int(r["est_minutes"]), int(r["actual_minutes"])))

    by_type: list[dict] = []
    for (subj, ttype), samples in agg.items():
        n = len(samples)
        avg_est = round(sum(e for e, _ in samples) / n, 1)
        avg_act = round(sum(a for _, a in samples) / n, 1)
        diff = round(avg_act - avg_est, 1)  # 正=低估, 负=高估
        diff_pct = round(diff / avg_est * 100, 1) if avg_est else 0.0
        by_type.append({
            "subject": subj,
            "task_type": ttype,
            "sample_size": n,
            "avg_est_minutes": avg_est,
            "avg_actual_minutes": avg_act,
            "diff_minutes": diff,
            "diff_pct": diff_pct,
            "advice": _make_advice(subj, ttype, diff, avg_est, avg_act),
        })

    # TOP 3 高估 (diff_minutes 最小, 实际远低于预估)
    over_sorted = sorted(by_type, key=lambda x: x["diff_minutes"])[:3]
    under_sorted = sorted(by_type, key=lambda x: -x["diff_minutes"])[:3]

    return {
        "week": week,
        "week_start": start.isoformat(),
        "week_end": end.isoformat(),
        "sample_size": sample_size,
        "by_type": by_type,
        "top_overestimate": [x for x in over_sorted if x["diff_minutes"] < 0],
        "top_underestimate": [x for x in under_sorted if x["diff_minutes"] > 0],
        "note": None,
    }


def _make_advice(subject: str, task_type: str, diff: float, avg_est: float, avg_act: float) -> str:
    """给家长一句话建议."""
    if abs(diff) < 3:
        return f"{subject}{task_type}估时基本准确 (估 {avg_est} 实 {avg_act})"
    if diff > 0:
        return f"{subject}{task_type}你平均估 {avg_est} 分钟, 实际 {avg_act} 分钟, 下次建议估 {round(avg_act)} 分钟"
    # diff < 0
    return f"{subject}{task_type}你平均估 {avg_est} 分钟, 实际 {avg_act} 分钟, 可下调到 {round(avg_act)} 分钟"