"""/api/export."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Query, Response

from ..services import export_service

router = APIRouter(prefix="/api", tags=["export"])


@router.get("/export")
def export_data() -> Response:
    body = export_service.export_all()
    ts = datetime.now().strftime("%Y%m%d-%H%M")
    filename = f"study-tracker-export-{ts}.json"
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/export/assignments.csv")
def export_assignments_csv(date: str | None = Query(None, description="周内任意一天, 默认今天")) -> Response:
    anchor = parse_date_param(date) if date else _today()
    body, filename = export_service.export_assignments_csv(anchor)
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )