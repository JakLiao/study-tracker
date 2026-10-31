"""/api/weekly-report (M6)."""
from __future__ import annotations

from fastapi import APIRouter, Query

from ..services import report_service
from ..services.assignment_service import parse_date_param
from ..services.report_service import _today

router = APIRouter(prefix="/api", tags=["report"])


@router.get("/weekly-report")
def weekly_report(date: str | None = Query(None, description="周内任意一天, 默认今天")) -> dict:
    anchor = parse_date_param(date) if date else _today()
    return report_service.build_weekly_report(anchor)
