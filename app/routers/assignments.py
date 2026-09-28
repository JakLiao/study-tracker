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


@router.get("/assignments")
def list_assignments(date: str = Query(...)) -> dict:
    d = assignment_service.parse_date_param(date)
    items = assignment_service.list_by_date(d)
    return {"date": d.isoformat(), "items": [i.model_dump(mode="json") for i in items]}


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