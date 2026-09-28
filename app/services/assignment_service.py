"""作业 (Assignment) 的 CRUD + 看板组装."""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .. import config
from ..db import get_conn
from ..errors import (
    AssignmentNotFoundError,
    BadDateFormatError,
    BadStatusError,
    BatchNotFoundError,
    DBWriteError,
    DeleteDoneForbiddenError,
    FieldOutOfRangeError,
    MinutesOutOfRangeError,
    MissingFieldError,
)
from ..models import AssignmentCreate, AssignmentDTO, AssignmentEdit, row_to_assignment_dto
from .normalize import dedup_fingerprint, normalize_task_name


# ====== 解析批次 ======

def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def create_parsed_batch(raw_text: str, model_version: str, result_count: int) -> tuple[int, bool]:
    """创建解析批次.

    Returns:
        (batch_id, is_duplicate)
    """
    text_hash = hash_text(raw_text)
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM parsed_batches WHERE raw_text_hash = ?", (text_hash,)
        ).fetchone()
        if existing:
            return existing["id"], True
        try:
            conn.execute("BEGIN")
            cur = conn.execute(
                "INSERT INTO parsed_batches (raw_text, raw_text_hash, parsed_at, ai_model_version, result_count, status) "
                "VALUES (?, ?, ?, ?, ?, 'pending')",
                (raw_text, text_hash, now, model_version, result_count),
            )
            conn.execute("COMMIT")
            return cur.lastrowid, False
        except Exception as e:
            conn.execute("ROLLBACK")
            raise DBWriteError(f"创建批次失败: {e}") from e


def confirm_batch(batch_id: int) -> None:
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM parsed_batches WHERE id = ?", (batch_id,)).fetchone()
        if not row:
            raise BatchNotFoundError()
        conn.execute("BEGIN")
        try:
            conn.execute("UPDATE parsed_batches SET status = 'confirmed' WHERE id = ?", (batch_id,))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


def discard_batch(batch_id: int) -> None:
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM parsed_batches WHERE id = ?", (batch_id,)).fetchone()
        if not row:
            raise BatchNotFoundError()
        conn.execute("BEGIN")
        try:
            conn.execute("UPDATE parsed_batches SET status = 'discarded' WHERE id = ?", (batch_id,))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


# ====== 入库 ======

def _log(conn, assignment_id: int, action: str, from_v: str | None, to_v: str | None) -> None:
    conn.execute(
        "INSERT INTO completion_logs (assignment_id, action, from_value, to_value, at) VALUES (?, ?, ?, ?, ?)",
        (assignment_id, action, from_v, to_v, datetime.now(timezone.utc).isoformat()),
    )


def insert_assignments(
    batch_id: int | None, items: list[AssignmentCreate]
) -> tuple[list[int], list[dict]]:
    """批量入库, 自动跳过重复作业.

    去重口径 (2026-09-28 用户确认): 同一科目 + 同一归属日 + 归一化后任务名相同
    即视为重复, 静默跳过不新建。跨天同名不算重复 —— 小学生背课文常常要背好几遍,
    跨天重复是常态。task_name 归一化能吸收全半角、大小写、空格、连字符、选做后缀的差异。

    Returns:
        (created_ids, skipped) —— created_ids 顺序与 items 中未被跳过的部分一致;
        skipped 为被跳过的条目摘要 [{task_name, subject, due_date, reason}, ...],
        供前端告知家长"跳过了 N 条重复"。
    """
    now = datetime.now(timezone.utc).isoformat()
    created_ids: list[int] = []
    skipped: list[dict] = []
    if not items:
        raise MissingFieldError("items 不能为空")

    # 字段预校验
    for idx, item in enumerate(items):
        if item.due_date is None:
            raise MissingFieldError(f"items[{idx}].due_date 必填 (AI 未识别, 家长必须确认)")
        if item.est_minutes < 1 or item.est_minutes > 180:
            raise FieldOutOfRangeError(f"items[{idx}].est_minutes 越界")
        if item.deadline_time and item.deadline_time.date() < item.due_date:
            raise FieldOutOfRangeError(f"items[{idx}].deadline_time 早于 due_date")

    if batch_id is not None:
        with get_conn() as conn:
            row = conn.execute("SELECT id FROM parsed_batches WHERE id = ?", (batch_id,)).fetchone()
            if not row:
                raise BatchNotFoundError()

    try:
        with get_conn() as conn:
            conn.execute("BEGIN")
            try:
                # 查重用的指纹集合。分两部分:
                #   seen  —— 本次批次内已处理的 (防同一次提交里自我重复)
                #   库中已有的 —— 按 (subject, due_date) 分组批量捞, 避免逐条查询
                seen: set[str] = set()

                # 先把涉及的 (subject, due_date) 组合捞出来, 一次查完而不是 N 次 SELECT
                combos: set[tuple[str, str]] = {
                    (item.subject, item.due_date.isoformat()) for item in items
                }
                existing_by_combo: dict[tuple[str, str], set[str]] = {}
                for subj, ddate in combos:
                    rows = conn.execute(
                        """
                        SELECT task_name FROM assignments
                        WHERE subject = ? AND due_date = ?
                        """,
                        (subj, ddate),
                    ).fetchall()
                    existing_by_combo[(subj, ddate)] = {
                        normalize_task_name(r["task_name"]) for r in rows
                    }

                for item in items:
                    due_iso = item.due_date.isoformat()
                    norm = normalize_task_name(item.task_name)
                    fp = dedup_fingerprint(item.subject, item.task_name, due_iso)

                    if fp in seen:
                        skipped.append({
                            "task_name": item.task_name,
                            "subject": item.subject,
                            "due_date": due_iso,
                            "reason": "本次提交内重复",
                        })
                        continue
                    if norm in existing_by_combo[(item.subject, due_iso)]:
                        skipped.append({
                            "task_name": item.task_name,
                            "subject": item.subject,
                            "due_date": due_iso,
                            "reason": "已存在相同作业",
                        })
                        continue

                    cur = conn.execute(
                        """
                        INSERT INTO assignments
                            (subject, task_name, due_date, deadline_time, is_optional,
                             est_minutes, actual_minutes, status, task_type, source_span,
                             confidence, batch_id, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, NULL, 'todo', ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            item.subject,
                            item.task_name,
                            due_iso,
                            item.deadline_time.isoformat() if item.deadline_time else None,
                            1 if item.is_optional else 0,
                            item.est_minutes,
                            item.task_type,
                            item.source_span or item.task_name,
                            item.confidence,
                            batch_id,
                            now,
                            now,
                        ),
                    )
                    new_id = cur.lastrowid
                    created_ids.append(new_id)
                    seen.add(fp)
                    existing_by_combo[(item.subject, due_iso)].add(norm)
                    _log(conn, new_id, "created", None, item.task_name)

                if batch_id is not None:
                    conn.execute(
                        "UPDATE parsed_batches SET status = 'confirmed' WHERE id = ?", (batch_id,)
                    )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
    except Exception as e:
        raise DBWriteError(f"入库失败: {e}") from e

    return created_ids, skipped


# ====== 查询 ======

def get_assignment(assignment_id: int) -> AssignmentDTO:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM assignments WHERE id = ?", (assignment_id,)).fetchone()
    if not row:
        raise AssignmentNotFoundError()
    return row_to_assignment_dto(row)


def list_by_date(d: date) -> list[AssignmentDTO]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM assignments WHERE due_date = ? ORDER BY subject, id",
            (d.isoformat(),),
        ).fetchall()
    return [row_to_assignment_dto(r) for r in rows]


def list_upcoming_deadlines(today: date, days: int = 7) -> list[AssignmentDTO]:
    """本周内非今日到期 (deadline_time 在 [today+1, today+days])."""
    end = today + timedelta(days=days)
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM assignments
            WHERE deadline_time IS NOT NULL
              AND date(deadline_time) > ?
              AND date(deadline_time) <= ?
              AND status != 'done'
            ORDER BY deadline_time, id
            """,
            (today.isoformat(), end.isoformat()),
        ).fetchall()
    return [row_to_assignment_dto(r) for r in rows]


# ====== 修改 ======

def update_status(assignment_id: int, new_status: str) -> dict:
    if new_status not in config.STATUSES:
        raise BadStatusError(f"status 必须是 {config.STATUSES}")
    with get_conn() as conn:
        row = conn.execute("SELECT status FROM assignments WHERE id = ?", (assignment_id,)).fetchone()
        if not row:
            raise AssignmentNotFoundError()
        old_status = row["status"]
        if old_status == new_status:
            return {"id": assignment_id, "status": new_status}
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("BEGIN")
        try:
            # 从 done 退回未完成状态时, 必须清掉 actual_minutes。
            # 否则会出现"待办作业却显示耗时 30 分"的矛盾展示,
            # 且这 30 分会混进周复盘统计, 污染估时偏差分析。
            if old_status == "done" and new_status != "done":
                conn.execute(
                    "UPDATE assignments SET status = ?, actual_minutes = NULL, updated_at = ? WHERE id = ?",
                    (new_status, now, assignment_id),
                )
                _log(conn, assignment_id, "edited", "actual_minutes", "null")
            else:
                conn.execute(
                    "UPDATE assignments SET status = ?, updated_at = ? WHERE id = ?",
                    (new_status, now, assignment_id),
                )
            _log(conn, assignment_id, "status_changed", old_status, new_status)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return {"id": assignment_id, "status": new_status}


def update_completion(assignment_id: int, actual_minutes: int) -> dict:
    if not (0 <= actual_minutes <= 240):
        raise MinutesOutOfRangeError("actual_minutes 必须在 0~240 之间")
    with get_conn() as conn:
        row = conn.execute(
            "SELECT status, actual_minutes FROM assignments WHERE id = ?", (assignment_id,)
        ).fetchone()
        if not row:
            raise AssignmentNotFoundError()
        old_val = str(row["actual_minutes"]) if row["actual_minutes"] is not None else None
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("BEGIN")
        try:
            conn.execute(
                "UPDATE assignments SET actual_minutes = ?, updated_at = ? WHERE id = ?",
                (actual_minutes, now, assignment_id),
            )
            _log(conn, assignment_id, "actual_logged", old_val, str(actual_minutes))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return {"id": assignment_id, "actual_minutes": actual_minutes}


def update_optional(assignment_id: int, is_optional: bool) -> dict:
    with get_conn() as conn:
        row = conn.execute("SELECT is_optional FROM assignments WHERE id = ?", (assignment_id,)).fetchone()
        if not row:
            raise AssignmentNotFoundError()
        old_val = "true" if row["is_optional"] else "false"
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("BEGIN")
        try:
            conn.execute(
                "UPDATE assignments SET is_optional = ?, updated_at = ? WHERE id = ?",
                (1 if is_optional else 0, now, assignment_id),
            )
            _log(conn, assignment_id, "edited", f"is_optional={old_val}", f"is_optional={is_optional}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return {"id": assignment_id, "is_optional": is_optional}


def edit_assignment(assignment_id: int, edit: AssignmentEdit) -> dict:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM assignments WHERE id = ?", (assignment_id,)).fetchone()
        if not row:
            raise AssignmentNotFoundError()
        fields: dict[str, Any] = {}
        old_summary: dict[str, Any] = {}
        new_summary: dict[str, Any] = {}
        if edit.task_name is not None:
            fields["task_name"] = edit.task_name
            old_summary["task_name"] = row["task_name"]
            new_summary["task_name"] = edit.task_name
        if edit.est_minutes is not None:
            fields["est_minutes"] = edit.est_minutes
            old_summary["est_minutes"] = row["est_minutes"]
            new_summary["est_minutes"] = edit.est_minutes
        if edit.task_type is not None:
            fields["task_type"] = edit.task_type
            old_summary["task_type"] = row["task_type"]
            new_summary["task_type"] = edit.task_type
        if edit.due_date is not None:
            fields["due_date"] = edit.due_date.isoformat()
            old_summary["due_date"] = row["due_date"]
            new_summary["due_date"] = edit.due_date.isoformat()
        if edit.deadline_time is not None:
            fields["deadline_time"] = edit.deadline_time.isoformat()
            old_summary["deadline_time"] = row["deadline_time"]
            new_summary["deadline_time"] = edit.deadline_time.isoformat()
        if edit.subject is not None:
            fields["subject"] = edit.subject
            old_summary["subject"] = row["subject"]
            new_summary["subject"] = edit.subject
        if not fields:
            return {"id": assignment_id, "noop": True}
        fields["updated_at"] = datetime.now(timezone.utc).isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        conn.execute("BEGIN")
        try:
            conn.execute(
                f"UPDATE assignments SET {set_clause} WHERE id = ?",
                (*fields.values(), assignment_id),
            )
            _log(conn, assignment_id, "edited", str(old_summary), str(new_summary))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return {"id": assignment_id, **{k: v for k, v in new_summary.items()}}


def delete_assignment(assignment_id: int) -> dict:
    with get_conn() as conn:
        row = conn.execute("SELECT status FROM assignments WHERE id = ?", (assignment_id,)).fetchone()
        if not row:
            raise AssignmentNotFoundError()
        if row["status"] == "done":
            raise DeleteDoneForbiddenError()
        conn.execute("BEGIN")
        try:
            _log(conn, assignment_id, "deleted", row["status"], None)
            conn.execute("DELETE FROM assignments WHERE id = ?", (assignment_id,))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return {"id": assignment_id, "deleted": True}


# ====== 看板 ======

def build_today_board(today: date) -> dict:
    is_late_night = datetime.now().hour < 5
    rows = list_by_date(today)
    by_subject: dict[str, list[AssignmentDTO]] = {s: [] for s in config.SUBJECTS}
    optional_items: list[AssignmentDTO] = []
    done = 0
    skipped = 0
    est_total = 0
    for r in rows:
        est_total += r.est_minutes or 0
        if r.is_optional:
            optional_items.append(r)
        else:
            by_subject[r.subject].append(r)
        if r.status == "done":
            done += 1
        elif r.status == "skipped":
            skipped += 1
    # 进度口径 (2026-09-28 用户确认): 跳过算"已收尾"但不当"完成",
    # 分母排除选做 —— 家长点过"选做"就不再是今天必须完成的事,
    # 不该让它把进度条永久压住走不满。
    required = len(rows) - len(optional_items)
    settled = done + skipped
    pct = 100 if required <= 0 else round(settled * 100 / required)
    upcoming = list_upcoming_deadlines(today, days=7)
    return {
        "date": today,
        "is_late_night": is_late_night,
        "progress": {
            "done": done,
            "skipped": skipped,
            "settled": settled,
            "total": len(rows),
            "optional": len(optional_items),
            "required": required,
            "pct": pct,
            "est_total": est_total,
        },
        "by_subject": by_subject,
        "optional_items": optional_items,
        "upcoming_deadlines": upcoming,
        "has_data": len(rows) > 0,
    }


def parse_date_param(s: str) -> date:
    try:
        return date.fromisoformat(s)
    except (ValueError, TypeError) as e:
        raise BadDateFormatError(f"日期格式错误: {s}") from e