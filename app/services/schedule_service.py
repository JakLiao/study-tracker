"""固定上课时段 (M5 周课表).

一张表存"哪个项目在星期几的几点到几点上课"。它的用途只有两件事:
  1. 周计划页算每天的总负荷 (课表时长 + 待办预估);
  2. 同一天两个时段重叠时给出提醒 —— 家长排课时最容易犯的错。

所有查询按 family_id 收口, 项目归属校验复用 project_service.project_exists,
与作业入库保持同一套租户规则。
"""
from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Any

from ..db import get_conn
from ..errors import (
    MissingFieldError,
    ProjectNotFoundError,
    ScheduleConflictError,
    ScheduleSlotNotFoundError,
)
from ..models import ScheduleSlotCreate, ScheduleSlotPatch
from .project_service import project_exists
from .tenant import default_family_id, ensure_default_family_child

_SLOT_SELECT = (
    "SELECT s.*, p.name AS project_name "
    "FROM schedule_slots s JOIN projects p ON p.id = s.project_id "
)


def _fmt_time(v: time | str) -> str:
    if isinstance(v, time):
        return v.strftime("%H:%M")
    return str(v)[:5]


def _minutes(start: time, end: time) -> int:
    return (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)


def _row_to_dict(row) -> dict[str, Any]:
    start, end = row["start_time"], row["end_time"]
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "project_name": row["project_name"],
        "weekday": int(row["weekday"]),
        "start_time": _fmt_time(start),
        "end_time": _fmt_time(end),
        "minutes": _minutes(start, end),
        "note": row["note"],
    }


def _parse_time(s: str) -> time:
    return time.fromisoformat(s)


def _find_conflict(
    conn, family_id: int, weekday: int, start: time, end: time,
    exclude_id: int | None = None,
):
    """同一天里与 [start, end) 重叠的第一条时段.

    判定用半开区间: 16:00-17:00 与 17:00-18:00 是首尾相接, 不算冲突;
    只有真正交叉 (start < 对方 end 且 end > 对方 start) 才报。
    """
    sql = (
        "SELECT s.id, s.start_time, s.end_time, p.name AS project_name "
        "FROM schedule_slots s JOIN projects p ON p.id = s.project_id "
        "WHERE s.family_id = %s AND s.weekday = %s"
    )
    params: list[Any] = [family_id, weekday]
    if exclude_id is not None:
        sql += " AND s.id <> %s"
        params.append(exclude_id)
    for row in conn.execute(sql, tuple(params)).fetchall():
        if start < row["end_time"] and end > row["start_time"]:
            return row
    return None


def _raise_conflict(row) -> None:
    raise ScheduleConflictError(
        f"与「{row['project_name']} "
        f"{_fmt_time(row['start_time'])}-{_fmt_time(row['end_time'])}」重叠"
    )


def list_slots() -> list[dict[str, Any]]:
    family_id = default_family_id()
    with get_conn() as conn:
        rows = conn.execute(
            _SLOT_SELECT + "WHERE s.family_id = %s ORDER BY s.weekday, s.start_time, s.id",
            (family_id,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def _get_row(conn, slot_id: int, family_id: int):
    return conn.execute(
        _SLOT_SELECT + "WHERE s.id = %s AND s.family_id = %s",
        (slot_id, family_id),
    ).fetchone()


def create_slot(req: ScheduleSlotCreate) -> dict[str, Any]:
    family_id, child_id = ensure_default_family_child()
    start, end = _parse_time(req.start_time), _parse_time(req.end_time)
    if end <= start:
        raise MissingFieldError("结束时间必须晚于开始时间")
    with get_conn() as conn:
        if not project_exists(conn, req.project_id, family_id):
            raise ProjectNotFoundError()
        conflict = _find_conflict(conn, family_id, req.weekday, start, end)
        if conflict:
            _raise_conflict(conflict)
        now = datetime.now(timezone.utc)
        cur = conn.execute(
            """
            INSERT INTO schedule_slots
                (family_id, child_id, project_id, weekday, start_time, end_time,
                 note, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (family_id, child_id, req.project_id, req.weekday, start, end,
             req.note, now, now),
        )
        new_id = cur.fetchone()["id"]
        row = _get_row(conn, new_id, family_id)
    return _row_to_dict(row)


def update_slot(slot_id: int, patch: ScheduleSlotPatch) -> dict[str, Any]:
    family_id = default_family_id()
    with get_conn() as conn:
        row = _get_row(conn, slot_id, family_id)
        if not row:
            raise ScheduleSlotNotFoundError()

        provided = patch.model_fields_set
        fields: dict[str, Any] = {}

        if "project_id" in provided:
            if patch.project_id is None:
                raise MissingFieldError("项目不能为空")
            if not project_exists(conn, patch.project_id, family_id):
                raise ProjectNotFoundError()
            fields["project_id"] = patch.project_id
        if "weekday" in provided:
            if patch.weekday is None:
                raise MissingFieldError("星期不能为空")
            fields["weekday"] = patch.weekday
        if "start_time" in provided:
            if patch.start_time is None:
                raise MissingFieldError("开始时间不能为空")
            fields["start_time"] = _parse_time(patch.start_time)
        if "end_time" in provided:
            if patch.end_time is None:
                raise MissingFieldError("结束时间不能为空")
            fields["end_time"] = _parse_time(patch.end_time)
        if "note" in provided:
            fields["note"] = patch.note

        if not fields:
            return _row_to_dict(row)

        weekday = fields.get("weekday", int(row["weekday"]))
        start = fields.get("start_time", row["start_time"])
        end = fields.get("end_time", row["end_time"])
        if end <= start:
            raise MissingFieldError("结束时间必须晚于开始时间")
        conflict = _find_conflict(conn, family_id, weekday, start, end, exclude_id=slot_id)
        if conflict:
            _raise_conflict(conflict)

        fields["updated_at"] = datetime.now(timezone.utc)
        set_clause = ", ".join(f"{k} = %s" for k in fields)
        conn.execute(
            f"UPDATE schedule_slots SET {set_clause} WHERE id = %s",
            (*fields.values(), slot_id),
        )
        row = _get_row(conn, slot_id, family_id)
    return _row_to_dict(row)


def delete_slot(slot_id: int) -> dict[str, Any]:
    family_id = default_family_id()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM schedule_slots WHERE id = %s AND family_id = %s",
            (slot_id, family_id),
        ).fetchone()
        if not row:
            raise ScheduleSlotNotFoundError()
        conn.execute("DELETE FROM schedule_slots WHERE id = %s", (slot_id,))
    return {"id": slot_id, "deleted": True}


def conflicts_in_day(day_slots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """一天内的重叠时段对. 时间都是 "HH:MM" 文本, 字典序比较即可."""
    out: list[dict[str, Any]] = []
    for i, a in enumerate(day_slots):
        for b in day_slots[i + 1:]:
            if a["start_time"] < b["end_time"] and a["end_time"] > b["start_time"]:
                out.append({
                    "slot_ids": [a["id"], b["id"]],
                    "message": (
                        f"{a['project_name']} {a['start_time']}-{a['end_time']} 与 "
                        f"{b['project_name']} {b['start_time']}-{b['end_time']} 重叠"
                    ),
                })
    return out
