"""/api/analytics/weekly."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Query

from ..services import analytics_service

router = APIRouter(prefix="/api", tags=["analytics"])


@router.get("/analytics/weekly")
def weekly_analytics(week: str = Query("current", pattern="^(current|last)$")) -> dict:
    today = datetime.now(timezone.utc).astimezone().date()
    result = analytics_service.build_weekly_analytics(week, today)
    return result