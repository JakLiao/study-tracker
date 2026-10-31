"""项目 (培训班 / 习惯) 的 CRUD.

M1 统一任务池: 学科作业之外的任务用 project_id 归类, 家长不必每次手打项目名。
所有查询都按 family_id 收口, 保持与看板/作业一致的租户隔离。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .. import config
from ..db import get_conn
from ..errors import MissingFieldError, ProjectNotFoundError
from ..models import ProjectCreate, ProjectDTO, ProjectPatch
from .tenant import default_family_id, ensure_default_family_child


def _row_to_dto(row) -> ProjectDTO:
    return ProjectDTO(
        id=row["id"],
        name=row["name"],
        project_type=row["project_type"],
        coach_name=row["coach_name"],
        schedule_note=row["schedule_note"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def list_projects(include_archived: bool = False) -> list[ProjectDTO]:
    family_id = default_family_id()
    with get_conn() as conn:
        if include_archived:
            rows = conn.execute(
                "SELECT * FROM projects WHERE family_id = %s ORDER BY status, id",
                (family_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM projects WHERE family_id = %s AND status != 'archived' ORDER BY status, id",
                (family_id,),
            ).fetchall()
    return [_row_to_dto(r) for r in rows]


def get_project(project_id: int) -> ProjectDTO:
    family_id = default_family_id()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM projects WHERE id = %s AND family_id = %s",
            (project_id, family_id),
        ).fetchone()
    if not row:
        raise ProjectNotFoundError()
    return _row_to_dto(row)


def create_project(payload: ProjectCreate) -> ProjectDTO:
    """创建项目. 同名同类型视为重复提交, 直接返回已有项目 (可安全重试)."""
    family_id, child_id = ensure_default_family_child()
    name = payload.name.strip()
    project_type = (payload.project_type or "custom").strip() or "custom"
    with get_conn() as conn:
        existing = conn.execute(
            """
            SELECT * FROM projects
            WHERE family_id = %s AND child_id = %s AND name = %s AND project_type = %s
            """,
            (family_id, child_id, name, project_type),
        ).fetchone()
        if existing:
            return _row_to_dto(existing)

        now = datetime.now(timezone.utc)
        cur = conn.execute(
            """
            INSERT INTO projects
                (family_id, child_id, name, project_type, coach_name, schedule_note,
                 status, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, 'active', %s, %s)
            RETURNING id
            """,
            (
                family_id,
                child_id,
                name,
                project_type,
                payload.coach_name,
                payload.schedule_note,
                now,
                now,
            ),
        )
        new_id = cur.fetchone()["id"]
        row = conn.execute("SELECT * FROM projects WHERE id = %s", (new_id,)).fetchone()
    return _row_to_dto(row)


def update_project(project_id: int, patch: ProjectPatch) -> ProjectDTO:
    family_id = default_family_id()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM projects WHERE id = %s AND family_id = %s",
            (project_id, family_id),
        ).fetchone()
        if not row:
            raise ProjectNotFoundError()

        provided = patch.model_fields_set
        fields: dict[str, Any] = {}
        if "name" in provided:
            if patch.name is None:
                raise MissingFieldError("项目名不能为空")
            fields["name"] = patch.name.strip()
        if "project_type" in provided:
            fields["project_type"] = (patch.project_type or "custom").strip() or "custom"
        if "coach_name" in provided:
            fields["coach_name"] = patch.coach_name
        if "schedule_note" in provided:
            fields["schedule_note"] = patch.schedule_note
        if "status" in provided:
            if patch.status is None:
                raise MissingFieldError("status 不能为空")
            fields["status"] = patch.status

        if not fields:
            return _row_to_dto(row)

        fields["updated_at"] = datetime.now(timezone.utc)
        set_clause = ", ".join(f"{k} = %s" for k in fields)
        conn.execute(
            f"UPDATE projects SET {set_clause} WHERE id = %s",
            (*fields.values(), project_id),
        )
        row = conn.execute("SELECT * FROM projects WHERE id = %s", (project_id,)).fetchone()
    return _row_to_dto(row)


def get_or_create_template_project(template_key: str) -> ProjectDTO:
    """按模板取/建项目. 供"一键生成默认任务"复用, 避免重复建同名项目."""
    template = next(
        (t for t in config.PROJECT_TEMPLATES if t["key"] == template_key), None
    )
    if not template:
        raise ProjectNotFoundError(f"模板不存在: {template_key}")
    return create_project(
        ProjectCreate(
            name=template["default_name"],
            project_type=template["project_type"],
        )
    )


def project_exists(conn, project_id: int, family_id: int) -> bool:
    """校验项目属于当前家庭. 写入作业前调用, 避免跨租户引用."""
    row = conn.execute(
        "SELECT id FROM projects WHERE id = %s AND family_id = %s",
        (project_id, family_id),
    ).fetchone()
    return bool(row)
