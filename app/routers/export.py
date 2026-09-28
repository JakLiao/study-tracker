"""/api/export."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Response

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