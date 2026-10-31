"""/api/assignments 路由族."""
from __future__ import annotations

from fastapi import APIRouter, Query

from ..errors import BatchNotFoundError
from ..models import (
    AssignmentCreate,
    AssignmentDTO,
    AssignmentEdit,
    BatchCreateRequest,
    BatchCreateResponse,
    CompletionPatch,
    OptionalPatch,
    QuickAddRequest,
    SkippedAssignment,
    StatusPatch,
)
from ..services import assignment_service

router = APIRouter(prefix="/api", tags=["assignments"])


@router.post("/assignments/batch", response_model=BatchCreateResponse)
def batch_create(req: BatchCreateRequest) -> BatchCreateResponse:
    items = [AssignmentCreate.model_validate(i) for i in req.items]
    ids, skipped = assignment_service.insert_assignments(req.batch_id, items)
    return BatchCreateResponse(
        created_ids=ids,
        skipped=[SkippedAssignment.model_validate(s) for s in skipped],
    )


@router.post("/assignments/quick")
def quick_add(req: QuickAddRequest) -> dict:
    """一键添加单条任务 (常做任务 / 项目模板). 重复添加返回 skipped=True."""
    return assignment_service.quick_add(req)


@router.get("/assignments")
def list_assignments(
    date: str = Query(...),
    task_kind: str | None = Query(None, description="subject / training / habit"),
    project_id: int | None = Query(None),
) -> dict:
    d = assignment_service.parse_date_param(date)
    items = assignment_service.list_by_date(d, task_kind=task_kind, project_id=project_id)
    return {"date": d.isoformat(), "items": [i.model_dump(mode="json") for i in items]}


@router.get("/assignments/calendar")
def calendar(
    year: int = Query(..., ge=1970, le=2100),
    month: int = Query(..., ge=1, le=12),
) -> dict:
    """按月返回每日作业汇总, 供日历视图渲染."""
    return assignment_service.calendar_summary(year, month)


@router.patch("/assignments/{assignment_id}/status")
def patch_status(assignment_id: int, body: StatusPatch) -> dict:
    return assignment_service.update_status(assignment_id, body.status)


@router.patch("/assignments/{assignment_id}/completion")
def patch_completion(assignment_id: int, body: CompletionPatch) -> dict:
    return assignment_service.update_completion(assignment_id, body.actual_minutes)


@router.patch("/assignments/{assignment_id}/optional")
def patch_optional(assignment_id: int, body: OptionalPatch) -> dict:
    return assignment_service.update_optional(assignment_id, body.is_optional)


@router.patch("/assignments/{assignment_id}")
def patch_assignment(assignment_id: int, body: AssignmentEdit) -> dict:
    return assignment_service.edit_assignment(assignment_id, body)


@router.delete("/assignments/{assignment_id}")
def delete_assignment(assignment_id: int) -> dict:
    return assignment_service.delete_assignment(assignment_id)