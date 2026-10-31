"""周计划 (M5): 每周固定节奏 + 一周负荷视图 + 一键生成任务.

两块内容:
  1. plan_items 的 CRUD —— 家长定义"每周一三五练琴 20 分钟"这类固定节奏;
  2. get_week_plan —— 把课表、计划项、当天真实任务合成 7 天视图,
     算每天的负荷并标出超载 / 冲突。生成时复用 assignment_service 的
     批量入库, 因此去重、项目归属校验、完成日志与手动录入完全同源。

口径说明:
  - remaining_minutes 只算 todo/doing 的预估, done 已经是过去投入,
    skipped 是家长主动不做, 都不该继续占今天的负荷。
  - 计划项若当天已经有同名任务 (同一去重范围), 就不再叠加它的预估,
    否则"已生成"的那一天会被算两遍, 负荷虚高。
  - 生成只覆盖今天及以后: 给过去的日子补任务是篡改历史, 看板与
    周复盘都会因此失真。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from .. import config
from ..db import get_conn
from ..errors import (
    BadTaskKindError,
    MissingFieldError,
    PlanItemNotFoundError,
    ProjectNotFoundError,
)
from ..models import AssignmentCreate, PlanItemCreate, PlanItemPatch
from .assignment_service import dedup_scope, insert_assignments
from .normalize import normalize_task_name
from .project_service import project_exists
from .schedule_service import conflicts_in_day, list_slots
from .tenant import default_family_id, ensure_default_family_child

_PLAN_SELECT = (
    "SELECT pi.*, p.name AS project_name "
    "FROM plan_items pi LEFT JOIN projects p ON p.id = pi.project_id "
)

_WEEKDAY_LABELS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def _weekdays_to_text(weekdays: list[int]) -> str:
    return ",".join(str(int(x)) for x in weekdays)


def _text_to_weekdays(text: str) -> list[int]:
    return [int(x) for x in str(text).split(",") if x.strip()]


def _plan_to_dict(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "task_kind": row["task_kind"],
        "subject": row["subject"],
        "project_id": row["project_id"],
        "project_name": row["project_name"],
        "task_name": row["task_name"],
        "est_minutes": row["est_minutes"],
        "weekdays": _text_to_weekdays(row["weekdays"]),
        "active": bool(row["active"]),
    }


def _get_plan_row(conn, item_id: int, family_id: int):
    return conn.execute(
        _PLAN_SELECT + "WHERE pi.id = %s AND pi.family_id = %s",
        (item_id, family_id),
    ).fetchone()


def list_plan_items(include_inactive: bool = False) -> list[dict[str, Any]]:
    family_id = default_family_id()
    sql = _PLAN_SELECT + "WHERE pi.family_id = %s"
    if not include_inactive:
        sql += " AND pi.active"
    sql += " ORDER BY pi.id"
    with get_conn() as conn:
        rows = conn.execute(sql, (family_id,)).fetchall()
    return [_plan_to_dict(r) for r in rows]


def create_plan_item(req: PlanItemCreate) -> dict[str, Any]:
    """新建计划项. 同范围同名视为重复提交, 更新星期与时长后返回 (可安全重试)."""
    family_id, child_id = ensure_default_family_child()
    with get_conn() as conn:
        if req.project_id is not None and not project_exists(conn, req.project_id, family_id):
            raise ProjectNotFoundError()

        candidates = conn.execute(
            _PLAN_SELECT
            + """
            WHERE pi.family_id = %s AND pi.task_kind = %s
              AND pi.subject IS NOT DISTINCT FROM %s
              AND pi.project_id IS NOT DISTINCT FROM %s
            """,
            (family_id, req.task_kind, req.subject, req.project_id),
        ).fetchall()
        norm = normalize_task_name(req.task_name)
        existing = next(
            (r for r in candidates if normalize_task_name(r["task_name"]) == norm), None
        )
        now = datetime.now(timezone.utc)
        if existing:
            conn.execute(
                "UPDATE plan_items SET weekdays = %s, est_minutes = %s, active = TRUE, "
                "updated_at = %s WHERE id = %s",
                (_weekdays_to_text(req.weekdays), req.est_minutes, now, existing["id"]),
            )
            row = _get_plan_row(conn, existing["id"], family_id)
            return {**_plan_to_dict(row), "updated": True}

        cur = conn.execute(
            """
            INSERT INTO plan_items
                (family_id, child_id, task_kind, subject, project_id, task_name,
                 est_minutes, weekdays, active, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s, %s)
            RETURNING id
            """,
            (family_id, child_id, req.task_kind, req.subject, req.project_id,
             req.task_name, req.est_minutes, _weekdays_to_text(req.weekdays), now, now),
        )
        new_id = cur.fetchone()["id"]
        row = _get_plan_row(conn, new_id, family_id)
    return {**_plan_to_dict(row), "updated": False}


def update_plan_item(item_id: int, patch: PlanItemPatch) -> dict[str, Any]:
    family_id = default_family_id()
    with get_conn() as conn:
        row = _get_plan_row(conn, item_id, family_id)
        if not row:
            raise PlanItemNotFoundError()

        provided = patch.model_fields_set
        fields: dict[str, Any] = {}
        if "task_kind" in provided:
            if patch.task_kind is None:
                raise MissingFieldError("任务类型不能为空")
            fields["task_kind"] = patch.task_kind
        if "subject" in provided:
            fields["subject"] = patch.subject
        if "project_id" in provided:
            fields["project_id"] = patch.project_id
        if "task_name" in provided:
            if patch.task_name is None:
                raise MissingFieldError("任务名不能为空")
            fields["task_name"] = patch.task_name
        if "est_minutes" in provided:
            if patch.est_minutes is None:
                raise MissingFieldError("预计用时不能为空")
            fields["est_minutes"] = patch.est_minutes
        if "weekdays" in provided:
            if patch.weekdays is None:
                raise MissingFieldError("至少选择一天")
            fields["weekdays"] = _weekdays_to_text(patch.weekdays)
        if "active" in provided:
            if patch.active is None:
                raise MissingFieldError("启用状态不能为空")
            fields["active"] = bool(patch.active)

        if not fields:
            return _plan_to_dict(row)

        # 归属三件套按"改后的有效状态"校验, 与作业编辑同一口径:
        # 只改 task_kind 时, subject/project 沿用的是库里的值, 必须一起判断。
        eff_kind = fields.get("task_kind", row["task_kind"])
        eff_subject = fields.get("subject", row["subject"])
        eff_project = fields.get("project_id", row["project_id"])
        if eff_kind == "subject":
            if eff_subject is None:
                raise MissingFieldError("学科任务必须选择科目")
            if eff_project is not None:
                raise BadTaskKindError("学科任务不能挂项目")
        else:
            if eff_subject is not None:
                raise BadTaskKindError("培训/习惯任务不能挂科目")
            if eff_project is not None and not project_exists(conn, eff_project, family_id):
                raise ProjectNotFoundError()

        fields["updated_at"] = datetime.now(timezone.utc)
        set_clause = ", ".join(f"{k} = %s" for k in fields)
        conn.execute(f"UPDATE plan_items SET {set_clause} WHERE id = %s", (*fields.values(), item_id))
        row = _get_plan_row(conn, item_id, family_id)
    return _plan_to_dict(row)


def delete_plan_item(item_id: int) -> dict[str, Any]:
    family_id = default_family_id()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM plan_items WHERE id = %s AND family_id = %s",
            (item_id, family_id),
        ).fetchone()
        if not row:
            raise PlanItemNotFoundError()
        conn.execute("DELETE FROM plan_items WHERE id = %s", (item_id,))
    return {"id": item_id, "deleted": True}


def _week_bounds(anchor: date) -> tuple[date, date]:
    start = anchor - timedelta(days=anchor.weekday())
    return start, start + timedelta(days=6)


def _load_limit(conn) -> int:
    row = conn.execute("SELECT value FROM settings WHERE key = 'daily_load_limit'").fetchone()
    if row:
        try:
            return max(0, int(str(row["value"]).strip()))
        except ValueError:
            pass
    return config.DEFAULT_DAILY_LOAD_MINUTES


def _assignment_snapshot(conn, family_id: int, start: date, end: date) -> dict:
    rows = conn.execute(
        """
        SELECT due_date, task_name, subject, task_kind, project_id,
               status, est_minutes, actual_minutes
        FROM assignments
        WHERE family_id = %s AND due_date >= %s AND due_date <= %s
        ORDER BY due_date, id
        """,
        (family_id, start, end),
    ).fetchall()
    stats: dict[str, dict[str, int]] = {}
    existing: dict[str, set[tuple[str, str]]] = {}
    for r in rows:
        ds = r["due_date"].isoformat() if hasattr(r["due_date"], "isoformat") else str(r["due_date"])
        s = stats.setdefault(ds, {
            "total": 0, "done": 0, "pending": 0,
            "remaining_minutes": 0, "actual_minutes": 0,
        })
        s["total"] += 1
        if r["status"] == "done":
            s["done"] += 1
            s["actual_minutes"] += (
                r["actual_minutes"] if r["actual_minutes"] is not None else r["est_minutes"]
            )
        elif r["status"] in ("todo", "doing"):
            s["pending"] += 1
            s["remaining_minutes"] += r["est_minutes"] or 0
        existing.setdefault(ds, set()).add((
            dedup_scope(r["subject"], r["task_kind"], r["project_id"]),
            normalize_task_name(r["task_name"]),
        ))
    return {"stats": stats, "existing": existing}


def get_week_plan(anchor: date) -> dict[str, Any]:
    today = datetime.now().astimezone().date()
    start, end = _week_bounds(anchor)
    family_id = default_family_id()
    slots = list_slots()
    plans = list_plan_items()
    with get_conn() as conn:
        limit = _load_limit(conn)
        snap = _assignment_snapshot(conn, family_id, start, end)

    days: list[dict[str, Any]] = []
    totals = {
        "slot_minutes": 0, "remaining_minutes": 0, "plan_pending_minutes": 0,
        "load_minutes": 0, "overloaded_days": 0, "conflict_days": 0,
    }
    for i in range(7):
        d = start + timedelta(days=i)
        ds = d.isoformat()
        day_slots = [s for s in slots if s["weekday"] == i]
        day_plans = [
            {**p, "generated": (
                dedup_scope(p["subject"], p["task_kind"], p["project_id"]),
                normalize_task_name(p["task_name"]),
            ) in snap["existing"].get(ds, set())}
            for p in plans if i in p["weekdays"]
        ]
        stats = snap["stats"].get(ds, {
            "total": 0, "done": 0, "pending": 0,
            "remaining_minutes": 0, "actual_minutes": 0,
        })
        slot_minutes = sum(s["minutes"] for s in day_slots)
        plan_pending = sum(p["est_minutes"] for p in day_plans if not p["generated"])
        load = slot_minutes + stats["remaining_minutes"] + plan_pending
        conflicts = conflicts_in_day(day_slots)
        day = {
            "date": ds,
            "weekday": i,
            "label": _WEEKDAY_LABELS[i],
            "is_today": d == today,
            "is_past": d < today,
            "slots": day_slots,
            "slot_minutes": slot_minutes,
            "plans": day_plans,
            "plan_pending_minutes": plan_pending,
            "assignments": stats,
            "load_minutes": load,
            "limit_minutes": limit,
            "overloaded": limit > 0 and load > limit,
            "conflicts": conflicts,
        }
        if day["overloaded"]:
            totals["overloaded_days"] += 1
        if conflicts:
            totals["conflict_days"] += 1
        totals["slot_minutes"] += slot_minutes
        totals["load_minutes"] += load
        days.append(day)
    totals["remaining_minutes"] = sum(d["assignments"]["remaining_minutes"] for d in days)
    totals["plan_pending_minutes"] = sum(d["plan_pending_minutes"] for d in days)
    return {
        "week_start": start.isoformat(),
        "week_end": end.isoformat(),
        "today": today.isoformat(),
        "daily_load_limit": limit,
        "days": days,
        "totals": totals,
        "slots": slots,
        "plan_items": plans,
    }


def generate_week(anchor: date, today: date | None = None) -> dict[str, Any]:
    """把周计划项展开成当周任务. 只生成今天及以后的日子, 重复调用不会重复建."""
    today = today or datetime.now().astimezone().date()
    start, end = _week_bounds(anchor)
    plans = list_plan_items()

    items: list[AssignmentCreate] = []
    for i in range(7):
        d = start + timedelta(days=i)
        if d < today:
            continue
        for p in plans:
            if i not in p["weekdays"]:
                continue
            items.append(AssignmentCreate(
                subject=p["subject"],
                task_kind=p["task_kind"],
                project_id=p["project_id"],
                task_name=p["task_name"],
                due_date=d,
                est_minutes=p["est_minutes"],
                source_span="周计划",
                confidence="high",
            ))

    if not items:
        return {
            "week_start": start.isoformat(), "week_end": end.isoformat(),
            "created_ids": [], "skipped": [],
            "created_count": 0, "skipped_count": 0,
        }

    created_ids, skipped = insert_assignments(None, items)
    return {
        "week_start": start.isoformat(), "week_end": end.isoformat(),
        "created_ids": created_ids, "skipped": skipped,
        "created_count": len(created_ids), "skipped_count": len(skipped),
    }
