"""作业 (Assignment) 的 CRUD + 看板组装."""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .. import config
from ..db import get_conn
from ..errors import (
    AppError,
    AssignmentNotFoundError,
    BadDateFormatError,
    BadStatusError,
    BadTaskKindError,
    BatchNotFoundError,
    DBWriteError,
    DeleteDoneForbiddenError,
    FieldOutOfRangeError,
    MinutesOutOfRangeError,
    MissingFieldError,
)
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
    now = datetime.now(timezone.utc)
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM parsed_batches WHERE raw_text_hash = %s", (text_hash,)
        ).fetchone()
        if existing:
            return existing["id"], True
        try:
            conn.execute("BEGIN")
            cur = conn.execute(
                "INSERT INTO parsed_batches (raw_text, raw_text_hash, parsed_at, ai_model_version, result_count, status) "
                "VALUES (%s, %s, %s, %s, %s, 'pending') RETURNING id",
                (raw_text, text_hash, now, model_version, result_count),
            )
            conn.execute("COMMIT")
            return cur.fetchone()["id"], False
        except Exception as e:
            conn.execute("ROLLBACK")
            raise DBWriteError(f"创建批次失败: {e}") from e


def confirm_batch(batch_id: int) -> None:
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM parsed_batches WHERE id = %s", (batch_id,)).fetchone()
        if not row:
            raise BatchNotFoundError()
        conn.execute("BEGIN")
        try:
            conn.execute("UPDATE parsed_batches SET status = 'confirmed' WHERE id = %s", (batch_id,))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


def discard_batch(batch_id: int) -> None:
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM parsed_batches WHERE id = %s", (batch_id,)).fetchone()
        if not row:
            raise BatchNotFoundError()
        conn.execute("BEGIN")
        try:
            conn.execute("UPDATE parsed_batches SET status = 'discarded' WHERE id = %s", (batch_id,))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


# ====== 入库 ======

def _log(conn, assignment_id: int, action: str, from_v: str | None, to_v: str | None) -> None:
    conn.execute(
        "INSERT INTO completion_logs (assignment_id, action, from_value, to_value, at) VALUES (%s, %s, %s, %s, %s)",
        (assignment_id, action, from_v, to_v, datetime.now(timezone.utc)),
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
    now = datetime.now(timezone.utc)
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

    family_id, child_id = ensure_default_family_child()

    if batch_id is not None:
        with get_conn() as conn:
            row = conn.execute("SELECT id FROM parsed_batches WHERE id = %s", (batch_id,)).fetchone()
            if not row:
                raise BatchNotFoundError()

    try:
        with get_conn() as conn:
            conn.execute("BEGIN")
            try:
                # 查重用的指纹集合。分两部分:
                #   seen  —— 本次批次内已处理的 (防同一次提交里自我重复)
                #   库中已有的 —— 按归属日一次捞全量, 再按 scope 分组, 避免逐条查询
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
                    if norm in existing_by_combo.get(combo, set()):
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
                            item.due_date,
                            item.deadline_time,
                            bool(item.is_optional),
                            item.est_minutes,
                            item.task_type,
                            item.source_span or item.task_name,
                            item.confidence,
                            batch_id,
                            now,
                            now,
                        ),
                    )
                    new_id = cur.fetchone()["id"]
                    created_ids.append(new_id)
                    seen.add(fp)
                    existing_by_combo.setdefault(combo, set()).add(norm)
                    _log(conn, new_id, "created", None, item.task_name)

                if batch_id is not None:
                    conn.execute(
                        "UPDATE parsed_batches SET status = 'confirmed' WHERE id = %s", (batch_id,)
                    )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
    except AppError:
        # 业务校验 (项目不属于本家庭等) 必须原样上抛, 不能被包成 5003,
        # 否则前端拿到的是"入库失败"而不是可定位的 4014。
        raise
    except Exception as e:
        raise DBWriteError(f"入库失败: {e}") from e

    return created_ids, skipped


# ====== 查询 ======

def get_assignment(assignment_id: int) -> AssignmentDTO:
    family_id = default_family_id()
    with get_conn() as conn:
        row = conn.execute(
            _ASSIGNMENT_WITH_PROJECT + "WHERE a.id = %s AND a.family_id = %s",
            (assignment_id, family_id),
        ).fetchone()
    if not row:
        raise AssignmentNotFoundError()
    return row_to_assignment_dto(row)


def list_by_date(d: date) -> list[AssignmentDTO]:
    with get_conn() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [row_to_assignment_dto(r) for r in rows]


def _month_specials(year: int, month: int, days_in_month: int) -> dict[str, dict[str, str]]:
    """当月需要角标提示的日子 (2026-10-26).

    给月历格子右上角挂小字角标用, 三类:
      holiday   法定假期 (国庆/中秋...), label/name 都是节日名
      makeup    调休上班日 (周末补班, 这天要上学), 角标只写"调休",
                完整节日名 (如"国庆调休") 放在 name 里, 悬停可见
      weekend   普通周末, label/name 都是"周末"

    判定顺序必须与周复盘页的 classify_day 一致: 假期 > 调休 > 周末。
    顺序反了会出现"国庆当天标成周末"—— 家长在月历上看到的分类会跟
    复盘页算出的上学日对不上, 同一件事在两个界面讲两个故事。

    与作业有无无关: 假期那几天本来就没作业, 但正是最需要标出来的时候。
    """
    holidays: dict[str, str] = {}
    for table in config.SCHOOL_HOLIDAYS_BY_YEAR.values():
        holidays.update(table)
    makeup: dict[str, str] = {}
    for table in config.SCHOOL_MAKEUP_CLASS_DAYS_BY_YEAR.values():
        makeup.update(table)

    out: dict[str, dict[str, str]] = {}
    for d in range(1, days_in_month + 1):
        day = date(year, month, d)
        ds = day.isoformat()
        if ds in holidays:
            out[ds] = {"kind": "holiday", "label": holidays[ds], "name": holidays[ds]}
        elif ds in makeup:
            out[ds] = {"kind": "makeup", "label": "调休", "name": makeup[ds]}
        elif day.weekday() >= 5:
            out[ds] = {"kind": "weekend", "label": "周末", "name": "周末"}
    return out


def calendar_summary(year: int, month: int) -> dict[str, Any]:
    """按月汇总每日作业数, 供日历视图渲染 (2026-09-28).

    一次查询返回整月, 避免前端按天发 30 次请求。
    口径与看板 (board) 保持一致:
      total          = 当天全部作业 (含选做)
      required       = 当天必做 (不含选做), 进度分母用这个
      required_done  = 必做已完成数
      required_skip  = 必做已跳过数 (跳过算"已收尾"但不算完成)
      optional       = 选做数
      optional_done  = 选做已完成数
      actual_total   = 当天总用时 (2026-10-15), 与看板 progress.actual_total 同口径
    另外返回 special: 当月的特殊日 (假期/调休/周末), 与作业有无无关,
    只给格子角标用 (2026-10-26)。
    必做与选做必须分开统计: 日历格子要判断"必做是否全完成",
    若只看合并的 done, 会出现"选做全做了、必做一项没做却显示全完成"的误判。
    """
    if not 1 <= month <= 12:
        raise BadDateFormatError(f"月份必须在 1-12 之间, 收到 {month}")
    first = date(year, month, 1)
    last = date(year + (month == 12), (month % 12) + 1, 1)
    family_id = default_family_id()
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT due_date,
                   COUNT(*)                                                    AS total,
                   COUNT(*) FILTER (WHERE is_optional)                         AS optional,
                   COUNT(*) FILTER (WHERE NOT is_optional AND status = 'done')    AS req_done,
                   COUNT(*) FILTER (WHERE NOT is_optional AND status = 'skipped') AS req_skip,
                   COUNT(*) FILTER (WHERE is_optional AND status = 'done')        AS opt_done,
                   COUNT(*) FILTER (WHERE is_optional AND status = 'skipped')     AS opt_skip,
                   -- 总用时: 只算真正做完的项, 跳过的没投入时间不能计。
                   -- 实耗为空时按预估计, 与 build_board_for 的 COALESCE 口径一致。
                   SUM(CASE WHEN status = 'done'
                            THEN COALESCE(actual_minutes, est_minutes)
                            ELSE 0 END)                                    AS act_total
            FROM assignments
            WHERE family_id = %s AND due_date >= %s AND due_date < %s
            GROUP BY due_date
            """,
            (family_id, first, last),
        ).fetchall()

    days: dict[str, Any] = {}
    for r in rows:
        d = r["due_date"]
        if isinstance(d, (date, datetime)):
            d = d.isoformat()
        optional = r["optional"] or 0
        req_done = r["req_done"] or 0
        req_skip = r["req_skip"] or 0
        required = r["total"] - optional
        days[d] = {
            "date": d,
            "total": r["total"],
            "required": required,
            "optional": optional,
            "required_done": req_done,
            "required_skipped": req_skip,
            "optional_done": r["opt_done"] or 0,
            "optional_skipped": r["opt_skip"] or 0,
            # 已收尾 = 完成 + 跳过 (跳过不计完成, 但当天不必再惦记)
            "settled": req_done + req_skip,
            "actual_total": r["act_total"] or 0,
        }
    return {
        "year": year,
        "month": month,
        "first_weekday": first.weekday(),  # 0=周一, 与前端 getDay() 对齐
        "days_in_month": (last - timedelta(days=1)).day,
        "days": days,
        # 特殊日角标 (2026-10-26): 假期 / 调休 / 周末, 与作业数据无关
        "special": _month_specials(year, month, (last - timedelta(days=1)).day),
    }


def list_upcoming_deadlines(today: date, days: int = 7) -> list[AssignmentDTO]:
    """本周内非今日到期 (deadline_time 在 [today+1, today+days])."""
    end = today + timedelta(days=days)
    family_id = default_family_id()
    with get_conn() as conn:
        rows = conn.execute(
            _ASSIGNMENT_WITH_PROJECT
            + """
            WHERE a.family_id = %s
              AND a.deadline_time IS NOT NULL
              AND a.deadline_time::date > %s
              AND a.deadline_time::date <= %s
              AND a.status != 'done'
            ORDER BY a.deadline_time, a.id
            """,
            (today.isoformat(), end.isoformat()),
        ).fetchall()
    return [row_to_assignment_dto(r) for r in rows]


# ====== 修改 ======

def update_status(assignment_id: int, new_status: str) -> dict:
    if new_status not in config.STATUSES:
        raise BadStatusError(f"status 必须是 {config.STATUSES}")
    family_id = default_family_id()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT status FROM assignments WHERE id = %s AND family_id = %s",
            (assignment_id, family_id),
        ).fetchone()
        if not row:
            raise AssignmentNotFoundError()
        old_status = row["status"]
        if old_status == new_status:
            return {"id": assignment_id, "status": new_status}
        now = datetime.now(timezone.utc)
        conn.execute("BEGIN")
        try:
            # 从 done 退回未完成状态时, 必须清掉 actual_minutes。
            # 否则会出现"待办作业却显示耗时 30 分"的矛盾展示,
            # 且这 30 分会混进周复盘统计, 污染估时偏差分析。
            if old_status == "done" and new_status != "done":
                conn.execute(
                    "UPDATE assignments SET status = %s, actual_minutes = NULL, updated_at = %s WHERE id = %s",
                    (new_status, now, assignment_id),
                )
                _log(conn, assignment_id, "edited", "actual_minutes", "null")
            else:
                conn.execute(
                    "UPDATE assignments SET status = %s, updated_at = %s WHERE id = %s",
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
    family_id = default_family_id()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT status, actual_minutes FROM assignments WHERE id = %s AND family_id = %s",
            (assignment_id, family_id),
        ).fetchone()
        if not row:
            raise AssignmentNotFoundError()
        old_val = str(row["actual_minutes"]) if row["actual_minutes"] is not None else None
        now = datetime.now(timezone.utc)
        conn.execute("BEGIN")
        try:
            conn.execute(
                "UPDATE assignments SET actual_minutes = %s, updated_at = %s WHERE id = %s",
                (actual_minutes, now, assignment_id),
            )
            _log(conn, assignment_id, "actual_logged", old_val, str(actual_minutes))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return {"id": assignment_id, "actual_minutes": actual_minutes}


def update_optional(assignment_id: int, is_optional: bool) -> dict:
    family_id = default_family_id()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT is_optional FROM assignments WHERE id = %s AND family_id = %s",
            (assignment_id, family_id),
        ).fetchone()
        if not row:
            raise AssignmentNotFoundError()
        old_val = "true" if row["is_optional"] else "false"
        now = datetime.now(timezone.utc)
        conn.execute("BEGIN")
        try:
            conn.execute(
                "UPDATE assignments SET is_optional = %s, updated_at = %s WHERE id = %s",
                (bool(is_optional), now, assignment_id),
            )
            _log(conn, assignment_id, "edited", f"is_optional={old_val}", f"is_optional={is_optional}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return {"id": assignment_id, "is_optional": is_optional}


def edit_assignment(assignment_id: int, edit: AssignmentEdit) -> dict:
    """按 PATCH 语义局部更新一条作业.

    判定"改哪些字段"必须看 model_fields_set 而不是"值是否为 None":
    两者在"清空字段"这个场景下会给出相反的结果。deadline_time 与 task_type
    都允许为空, 家长在弹窗里删掉截止时间后会传 deadline_time: null ——
    若按 `is not None` 判断, 这次清空会被当成"没传"而静默忽略, 家长
    看到弹窗关了以为改成功, 刷新后截止时间又回来了。

    反过来, due_date / est_minutes / task_name / subject 是 NOT NULL 列,
    传 null 属于非法输入, 直接拒掉而不是让 SQLite 抛 IntegrityError。
    """
    family_id = default_family_id()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM assignments WHERE id = %s AND family_id = %s",
            (assignment_id, family_id),
        ).fetchone()
        if not row:
            raise AssignmentNotFoundError()

        provided = edit.model_fields_set
        fields: dict[str, Any] = {}
        old_summary: dict[str, Any] = {}
        new_summary: dict[str, Any] = {}

        def _put(key: str, value: Any, old: Any) -> None:
            fields[key] = value
            old_summary[key] = old
            new_summary[key] = value

        # --- NOT NULL 列: 传 null 视为非法 ---
        for key, val in (
            ("task_name", edit.task_name),
            ("est_minutes", edit.est_minutes),
            ("due_date", edit.due_date),
        ):
            if key in provided and val is None:
                raise MissingFieldError(f"{key} 不能为空")

        if edit.task_name is not None:
            _put("task_name", edit.task_name, row["task_name"])
        if edit.est_minutes is not None:
            _put("est_minutes", edit.est_minutes, row["est_minutes"])
        if edit.due_date is not None:
            _put("due_date", edit.due_date.isoformat(), row["due_date"])

        # --- 可空列: 出现即写入, null 即清空 ---
        # task_type 用空串兜成 null: 表单里清空输入框传出的是 "",
        # 直接写库会留下一条"有类型但内容为空"的脏数据, 而周复盘按
        # task_type 分组, 空串会单独占一组。
        if "task_type" in provided:
            _put("task_type", (edit.task_type or "").strip() or None, row["task_type"])
        if "deadline_time" in provided:
            _put(
                "deadline_time",
                edit.deadline_time,
                row["deadline_time"],
            )
        # is_optional 同理是"出现即写入", 但它不会为 null —— 表单里只有
        # 勾选/不勾选两种, 前端传的是 true/false。判定用 is not None 而不是
        # if edit.is_optional: 后者会把「取消选做」(False) 当成没传, 勾选
        # 永远取消不掉。
        if edit.is_optional is not None:
            _put("is_optional", bool(edit.is_optional), row["is_optional"])

        if not fields:
            return {"id": assignment_id, "noop": True}

        # 归属日与截止时间的先后关系要在落库前校验。
        # 单独改 due_date 而不动 deadline_time 时, 新的归属日可能已经晚于
        # 原截止时间 —— 这条作业从此变成"截止时间在归属日之前", 而看板的
        # "未来 7 天到期"提醒正是按 deadline_time 排序的, 会算出错的提示。
        # 口径与 insert_assignments 保持一致。
        new_due = fields.get("due_date", row["due_date"])
        new_dl = fields.get("deadline_time", row["deadline_time"])
        due_val = _as_date(new_due)
        dl_val = _as_datetime(new_dl)
        if dl_val and due_val and dl_val.date() < due_val:
            raise FieldOutOfRangeError("deadline_time 早于 due_date")

        fields["updated_at"] = datetime.now(timezone.utc)
        set_clause = ", ".join(f"{k} = %s" for k in fields)
        conn.execute("BEGIN")
        try:
            conn.execute(
                f"UPDATE assignments SET {set_clause} WHERE id = %s",
                (*fields.values(), assignment_id),
            )
            _log(conn, assignment_id, "edited", str(old_summary), str(new_summary))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return {"id": assignment_id, **new_summary}


def delete_assignment(assignment_id: int) -> dict:
    family_id = default_family_id()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT status FROM assignments WHERE id = %s AND family_id = %s",
            (assignment_id, family_id),
        ).fetchone()
        if not row:
            raise AssignmentNotFoundError()
        if row["status"] == "done":
            raise DeleteDoneForbiddenError()
        conn.execute("BEGIN")
        try:
            _log(conn, assignment_id, "deleted", row["status"], None)
            conn.execute("DELETE FROM assignments WHERE id = %s", (assignment_id,))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return {"id": assignment_id, "deleted": True}


# ====== 看板 ======

def list_recent_dates(limit: int = 3, today: date | None = None) -> list[dict]:
    """最近 N 个日期, 倒序 (最新在前)。"今天"永远在第一位。

    除了今天以外只取"有作业"的日期, 而不是日历日 (2026-09-28 用户确认):
    长假期间 (国庆、春节) 连续多天没有作业, 若按日历日取, 看板会连着
    几天空白, 白白占掉 3 个位置里的 2 个。改成只取有数据的日期, 这样
    无论是否放假, 打开看板永远能看到最近 3 天的实际记录。

    但"今天"必须无条件保留 (2026-09-29 用户反馈):
    SQL 的 GROUP BY due_date 会把零作业的日子整个抹掉, 于是今天没
    作业时第一张卡变成"昨天", 而前端默认高亮第一张 —— 深色底 + "昨天"
    会被读成"今天是空白的一天", 家长以为今天没作业被漏掉了, 实际是
    昨天有作业而今天确实没有。现在补一张 total=0 的今天卡, 深色底
    才对得上"今天", 下面的空状态("今天还没有作业")也有了对应的 Tab。

    每项含轻量统计, 供前端直接渲染 Tab 徽标, 不必再为每一天单独请求看板。
    """
    if limit < 1:
        return []
    today = today or datetime.now().astimezone().date()
    family_id = default_family_id()
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT due_date,
                   COUNT(*)                                            AS total,
                   COUNT(*) FILTER (WHERE status = 'done')             AS done,
                   COUNT(*) FILTER (WHERE status = 'skipped')          AS skipped,
                   COUNT(*) FILTER (WHERE is_optional)                 AS optional
            FROM assignments
            WHERE family_id = %s AND due_date <= %s
            GROUP BY due_date
            ORDER BY due_date DESC
            LIMIT %s
            """,
            (family_id, today, limit),
        ).fetchall()
    out: list[dict] = []
    for r in rows:
        raw_d = r["due_date"]
        d = raw_d if isinstance(raw_d, date) and not isinstance(raw_d, datetime) else _as_date(raw_d)
        ds = d.isoformat()
        out.append({
            "date": ds,
            "label": _date_label(d, today),
            "total": r["total"] or 0,
            "done": r["done"] or 0,
            "skipped": r["skipped"] or 0,
            "optional": r["optional"] or 0,
            "is_today": d == today,
            "has_data": True,
        })
    # 今天没有作业时 SQL 查不到它, 补一张空卡占住第一位。
    # 补完再截到 limit: 否则会变成 limit+1 张卡, 让 ?limit=3 渲染出 4 张,
    # 挤掉本该保留的历史记录。丢掉的是最旧的那天, 代价最小。
    if not out or out[0]["date"] != today.isoformat():
        out.insert(0, {
            "date": today.isoformat(),
            "label": "今天",
            "total": 0,
            "done": 0,
            "skipped": 0,
            "optional": 0,
            "is_today": True,
            "has_data": False,
        })
        out = out[:limit]
    return out


_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def _date_label(d: date, today: date) -> str:
    """Tab 上的日期标签: 今天 / 昨天 / 9月22日 周二."""
    delta = (today - d).days
    if delta == 0:
        return "今天"
    if delta == 1:
        return "昨天"
    if 1 < delta <= 6:
        return f"前天" if delta == 2 else f"{delta} 天前"
    return f"{d.month}月{d.day}日"


def build_board_for(d: date, today: date | None = None) -> dict:
    """指定日期的看板. 与 build_today_board 同结构, 供历史日期 Tab 复用."""
    is_late_night = datetime.now().hour < 5
    rows = list_by_date(d)
    by_subject: dict[str, list[AssignmentDTO]] = {s: [] for s in config.SUBJECTS}
    optional_items: list[AssignmentDTO] = []
    done = 0
    skipped = 0
    opt_done = 0
    opt_skipped = 0
    est_total = 0
    actual_total = 0
    for r in rows:
        est_total += r.est_minutes or 0
        # 总用时 (2026-10-09 用户要求"显出孩子的努力成果"):
        # 只累计真正做完的项 —— 跳过的项没投入时间, 计进去等于凭空
        # 替孩子加分, 与"努力成果"的本意相反。
        # 选做做了就是做了, 计入 (与周复盘偏差统计口径不同, 那里排
        # 选做是为了让估时更准; 这里是记录投入, 不是评估估时质量)。
        # actual 为空时按预估计, 与周复盘 COALESCE 口径一致。
        if r.status == "done":
            actual_total += r.actual_minutes if r.actual_minutes is not None else (r.est_minutes or 0)
        if r.is_optional:
            optional_items.append(r)
            if r.status == "done":
                opt_done += 1
            elif r.status == "skipped":
                opt_skipped += 1
        else:
            by_subject[r.subject].append(r)
            # 进度分子只数必做。原实现在这里统计全部 rows, 而下方分母
            # required 却减掉了选做 —— 分子分母不同源, 于是选做一旦完成
            # 就会把进度凭空抬高: 必做还剩 1 项没做时显示 7/7 = 100%。
            # 月历 daily_summary 一直是按 is_optional 分流的(见 L265-268),
            # 这里对齐它, 两个界面的进度才会一致。
            if r.status == "done":
                done += 1
            elif r.status == "skipped":
                skipped += 1
    # 进度口径 (2026-09-28 用户确认): 跳过算"已收尾"但不当"完成",
    # 分母排除选做 —— 家长点过"选做"就不再是今天必须完成的事,
    # 不该让它把进度条永久压住走不满。
    # 分子 done/skipped 同样只数必做(见上方循环), 与分母同源。
    required = len(rows) - len(optional_items)
    settled = done + skipped
    # 三种情况要分开, 不能一律按 required<=0 给 100:
    #   有必做   -> 正常算, 并用 min 封顶防溢出(前端 width 撑破会变空条)
    #   全是选做 -> 没有必做可收尾, 算作已走完
    #   当天没作业 -> 不该报 100%, 否则调用方会以为进度已满
    # 目前空看板会提前 return 不渲染进度条, 所以后两种在界面上看不到,
    # 但接口语义必须正确 —— 它已经是 /api/board 的公开返回值。
    if required > 0:
        pct = min(100, round(settled * 100 / required))
    else:
        pct = 100 if rows else 0
    today = today or datetime.now().astimezone().date()
    # 截止时间提醒只对"今天/未来"有意义, 看历史日期时不显示
    upcoming = list_upcoming_deadlines(today, days=7) if d >= today else []
    return {
        "date": d,
        "label": _date_label(d, today),
        "is_today": d == today,
        "is_late_night": is_late_night,
        "progress": {
            # done/skipped 是必做的完成与跳过数, 与 required 同源。
            # 选做的完成情况另计 —— 它不参与进度, 但副行要显示"选做 1/1"。
            "done": done,
            "skipped": skipped,
            "settled": settled,
            "total": len(rows),
            "optional": len(optional_items),
            "optional_done": opt_done,
            "optional_skipped": opt_skipped,
            "required": required,
            "pct": pct,
            "est_total": est_total,
            "actual_total": actual_total,
        },
        "by_subject": by_subject,
        "optional_items": optional_items,
        "upcoming_deadlines": upcoming,
        "has_data": len(rows) > 0,
    }


def build_today_board(today: date) -> dict:
    """今天的看板. 保留此函数以兼容既有调用方."""
    return build_board_for(today, today)


def parse_date_param(s: str) -> date:
    try:
        return date.fromisoformat(s)
    except (ValueError, TypeError) as e:
        raise BadDateFormatError(f"日期格式错误: {s}") from e