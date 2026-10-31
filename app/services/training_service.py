"""训练打卡 (M4): 培训任务的 30 秒打卡.

口径:
  - 只有 task_kind=training 的任务能打卡。学科作业的"完成"走状态接口,
    习惯打卡是 F29 的另一个范围, 不在这里混入。
  - 打卡即完成: 打卡记录与看板的 done/actual_minutes 必须一致, 否则会
    出现"打卡说练了 30 分钟, 看板还显示待完成"的两套真相。
  - log_date 取任务归属日而不是打卡当天的日期: 家长偶尔第二天早上补
    记昨晚的练习, 记录仍应归到昨晚, 否则周报会把这次练习算到错误的
    一天 (看板本来就是按 due_date 分组的)。
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from ..db import get_conn
from ..errors import AssignmentNotFoundError, BadTaskKindError, DBWriteError
from ..models import TrainingLogSave
from .tenant import default_family_id, ensure_default_family_child


def _row_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "assignment_id": row["assignment_id"],
        "project_id": row["project_id"],
        # JOIN 出来的展示字段; 单独查 training_logs 时没有这两列。
        "project_name": row.get("project_name"),
        "task_name": row.get("task_name"),
        "log_date": row["log_date"],
        "duration_minutes": row["duration_minutes"],
        "sets": row["sets"],
        "reps": row["reps"],
        "segment": row["segment"],
        "note": row["note"],
        "updated_at": row["updated_at"],
    }


_WITH_LABELS = (
    "SELECT tl.*, a.task_name, p.name AS project_name "
    "FROM training_logs tl "
    "JOIN assignments a ON a.id = tl.assignment_id "
    "LEFT JOIN projects p ON p.id = tl.project_id "
)


def save_log(req: TrainingLogSave) -> dict:
    """打卡 (upsert). 同一任务重复打卡按最后一次覆盖."""
    family_id, child_id = ensure_default_family_child()
    with get_conn() as conn:
        a = conn.execute(
            """
            SELECT id, status, task_kind, project_id, due_date
            FROM assignments
            WHERE id = %s AND family_id = %s
            """,
            (req.assignment_id, family_id),
        ).fetchone()
        if not a:
            raise AssignmentNotFoundError()
        if a["task_kind"] != "training":
            raise BadTaskKindError("只有培训班任务可以打卡")

        now = datetime.now(timezone.utc)
        conn.execute("BEGIN")
        try:
            conn.execute(
                """
                INSERT INTO training_logs
                    (family_id, child_id, assignment_id, project_id, log_date,
                     duration_minutes, sets, reps, segment, note,
                     created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (assignment_id) DO UPDATE SET
                    duration_minutes = EXCLUDED.duration_minutes,
                    sets             = EXCLUDED.sets,
                    reps             = EXCLUDED.reps,
                    segment          = EXCLUDED.segment,
                    note             = EXCLUDED.note,
                    updated_at       = EXCLUDED.updated_at
                """,
                (
                    family_id,
                    child_id,
                    req.assignment_id,
                    a["project_id"],
                    a["due_date"],
                    req.duration_minutes,
                    req.sets,
                    req.reps,
                    req.segment,
                    req.note,
                    now,
                    now,
                ),
            )
            # 打卡即完成。实耗取打卡时长: 家长填的时长就是这次训练真正
            # 花掉的时间, 比预估更可信, 周报的估时偏差才有意义。
            conn.execute(
                "UPDATE assignments SET status = 'done', actual_minutes = %s, updated_at = %s "
                "WHERE id = %s",
                (req.duration_minutes, now, req.assignment_id),
            )
            # completion_logs 的 action 有 CHECK 约束 ('status_changed' 等),
            # 不再新增枚举值: 打卡的明细 (时长/组数/段落) 本身就在
            # training_logs 里, 这里只记"状态从 X 变成 done"这一件事。
            conn.execute(
                "INSERT INTO completion_logs (assignment_id, action, from_value, to_value, at) "
                "VALUES (%s, 'status_changed', %s, 'done', %s)",
                (req.assignment_id, a["status"], now),
            )
            conn.execute("COMMIT")
        except Exception as e:
            conn.execute("ROLLBACK")
            raise DBWriteError(f"训练打卡失败: {e}") from e

        row = conn.execute(
            _WITH_LABELS + "WHERE tl.assignment_id = %s AND tl.family_id = %s",
            (req.assignment_id, family_id),
        ).fetchone()

    return {
        "log": _row_to_dict(row),
        "assignment_id": req.assignment_id,
        "status": "done",
        "actual_minutes": req.duration_minutes,
    }


def logs_by_assignment(d: date) -> dict[int, dict]:
    """某天每个任务的最新打卡, 键为 assignment_id.

    看板用它在训练任务行上直接显示"已打卡 30 分 · 3 组 × 20",
    免得每行再发一次请求。
    """
    family_id = default_family_id()
    with get_conn() as conn:
        rows = conn.execute(
            _WITH_LABELS + "WHERE tl.family_id = %s AND tl.log_date = %s ORDER BY tl.id",
            (family_id, d),
        ).fetchall()
    return {r["assignment_id"]: _row_to_dict(r) for r in rows}


def list_logs(d: date) -> list[dict]:
    """某天的打卡记录 (带任务名/项目名), 供"改打卡"预填与后续周报复用."""
    family_id = default_family_id()
    with get_conn() as conn:
        rows = conn.execute(
            _WITH_LABELS + "WHERE tl.family_id = %s AND tl.log_date = %s ORDER BY tl.id",
            (family_id, d),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]
