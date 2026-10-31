"""数据导出 (JSON 全量快照 + 本周作业清单 CSV)."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from ..db import get_conn
from ..errors import ExportError
from .tenant import default_family_id

WEEKDAY_NAMES = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
STATUS_LABELS = {"todo": "待完成", "doing": "进行中", "done": "完成", "skipped": "跳过"}
TASK_KIND_LABELS = {"subject": "学科", "training": "培训", "habit": "习惯"}


def _csv_cell(value) -> str:
    """CSV 单元格转义: 含逗号/引号/换行时加引号, 引号自身翻倍."""
    text = "" if value is None else str(value)
    if any(ch in text for ch in (",", '"', "\n", "\r")):
        return '"' + text.replace('"', '""') + '"'
    return text


def export_assignments_csv(anchor: date) -> tuple[bytes, str]:
    """一周作业清单 (可打印). 返回 (CSV 字节, 建议文件名).

    UTF-8 BOM 前缀是给 Excel 的: 没有 BOM 时 Windows 版 Excel 会按本地
    代码页打开, 中文全是乱码。用 CRLF 换行同样是 Excel 的兼容选择。
    """
    start = anchor - timedelta(days=anchor.weekday())
    end = start + timedelta(days=6)
    family_id = default_family_id()
    try:
        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT a.due_date, a.subject, a.task_kind, a.task_name,
                       a.est_minutes, a.actual_minutes, a.status,
                       a.is_optional, a.deadline_time, p.name AS project_name
                FROM assignments a
                LEFT JOIN projects p ON p.id = a.project_id
                WHERE a.family_id = %s AND a.due_date BETWEEN %s AND %s
                ORDER BY a.due_date, a.task_kind, a.subject, a.id
                """,
                (family_id, start, end),
            ).fetchall()
    except Exception as e:
        raise ExportError(f"导出失败: {e}") from e

    header = ["日期", "星期", "类型", "科目/项目", "任务", "预计分钟",
              "实际分钟", "状态", "选做", "截止时间"]
    lines = [",".join(_csv_cell(h) for h in header)]
    for r in rows:
        due = r["due_date"]
        lines.append(",".join(_csv_cell(v) for v in (
            due.isoformat(),
            WEEKDAY_NAMES[due.weekday()],
            TASK_KIND_LABELS.get(r["task_kind"], r["task_kind"]),
            r["subject"] or r["project_name"] or "",
            r["task_name"],
            r["est_minutes"],
            r["actual_minutes"] if r["actual_minutes"] is not None else "",
            STATUS_LABELS.get(r["status"], r["status"]),
            "是" if r["is_optional"] else "否",
            r["deadline_time"].strftime("%H:%M") if r["deadline_time"] else "",
        )))
    body = ("\ufeff" + "\r\n".join(lines) + "\r\n").encode("utf-8")
    return body, f"study-tracker-week-{start.isoformat()}.csv"


def export_all() -> bytes:
    family_id = default_family_id()
    try:
        with get_conn() as conn:
            assignments = [dict(r) for r in conn.execute(
                "SELECT * FROM assignments WHERE family_id = %s ORDER BY due_date, id",
                (family_id,),
            ).fetchall()]
            # M1: 项目是作业的归属来源, 不导出的话再导入会丢掉"这条是钢琴还是篮球"。
            projects = [dict(r) for r in conn.execute(
                "SELECT * FROM projects WHERE family_id = %s ORDER BY id",
                (family_id,),
            ).fetchall()]
            batches = [dict(r) for r in conn.execute(
                "SELECT * FROM parsed_batches ORDER BY id"
            ).fetchall()]
            logs = [dict(r) for r in conn.execute(
                "SELECT * FROM completion_logs WHERE assignment_id IN "
                "(SELECT id FROM assignments WHERE family_id = %s) ORDER BY at",
                (family_id,),
            ).fetchall()]
            settings = [dict(r) for r in conn.execute(
                "SELECT * FROM settings ORDER BY key"
            ).fetchall()]
            # M3: 晚间三问是周报归因的唯一来源, 不导出的话换机后
            # "孩子为什么慢" 的那一半数据就丢了。
            reflections = [dict(r) for r in conn.execute(
                (family_id,),
            ).fetchall()]
            # M4: 训练打卡是训练线周报的唯一数据源, 换机后丢了就接不上
            # "这周练了几次、曲子推进到哪一段"。
                (family_id,),
            ).fetchall()]
            # M5: 课表与周计划项是"每周节奏"的配置, 换机后不导出就得重排一遍。
                (family_id,),
            ).fetchall()]
                (family_id,),
            ).fetchall()]
        payload = {
            "schema_version": 1,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "counts": {
                "assignments": len(assignments),
                "projects": len(projects),
                "batches": len(batches),
                "logs": len(logs),
                "settings": len(settings),
                "reflections": len(reflections),
            },
            "assignments": assignments,
            "projects": projects,
            "parsed_batches": batches,
            "completion_logs": logs,
            "settings": settings,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode("utf-8")
    except Exception as e:
        raise ExportError(f"导出失败: {e}") from e